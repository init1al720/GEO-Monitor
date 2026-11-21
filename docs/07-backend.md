# 后端采集与分析服务 v0.1

数据层是 Supabase 直连，所以「后端」不是一套 REST 接口，而是三件事：**去各家引擎问问题 → 判断回答里提到了谁 → 算成每天的指标**。这份文档说明代码怎么组织、怎么跑、怎么扩。

## 一、代码结构

```
app/
├─ cli.py                 命令行入口（init / check / seed / collect / metrics / reanalyze）
├─ core/
│  ├─ config.py           环境变量（数据库串、各引擎密钥、并发与超时）
│  ├─ db.py               异步 engine + session 工厂
│  └─ logging.py          结构化日志（沿用框架那套，带 request_id）
├─ models/
│  ├─ base.py             声明式基类 + JSON 类型（PG 用 jsonb，SQLite 退化为 JSON）
│  └─ geo.py              6 张表：projects / prompts / runs / responses / mentions / daily_metrics
├─ engines/
│  ├─ base.py             EngineClient 协议 + EngineAnswer 统一结果
│  ├─ openai_compat.py    OpenAI 兼容实现（DeepSeek / 通义 / 豆包共用）
│  └─ registry.py         引擎注册表：密钥、模型、计费单价
└─ services/
   ├─ collector.py        run_once：并发调引擎 → 串行落库 → 当场做提及分析
   ├─ analyzer.py         纯函数：品牌命中、位次、情绪、引用域名
   ├─ metrics.py          聚合：responses + mentions → daily_metrics
   └─ seed.py             造演示数据（不花钱也能让看板出数）
```

## 二、怎么跑

```bash
# 0. 装依赖（首次）
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# 1. 看密钥配置情况
python -m app.cli engines

# 2. 本地演示：建表 + 灌 7 天示例数据，不花一分钱
python -m app.cli init
python -m app.cli seed --days 7

# 3. 自检：连通性 + 各表行数
python -m app.cli check

# 4. 真采集（需先在 .env 配好 DEEPSEEK_API_KEY）
python -m app.cli collect --project 演示项目 --limit 2 --dry-run   # 先试跑不写库
python -m app.cli collect --project 演示项目 --metrics             # 采集并算当天指标

# 5. 规则改了，重跑解析（不重新调引擎，不花钱）
python -m app.cli reanalyze --run <run_id>
```

线上把 `DATABASE_URL` 换成 Supabase 的连接串即可，代码不用改。**线上建表用 `docs/sql/01-schema.sql`，不要用 `cli init`**——后者只建表，没有视图、没有 RLS。

## 三、三个刻意的设计

**1. HTTP 并发，写库串行。** 20 个问题 × 3 家引擎串行要十几分钟；并发抓完再按顺序写只要一两分钟。之所以不边抓边写：同一个 `AsyncSession` 不能被多个协程同时使用，会直接报错。

**2. 失败不中断，失败也进库。** 单条失败只写一条 `status != 'success'` 的 `responses`，整轮 `run.status` 记 `partial`。这样「今天指标为什么掉了」能查到是引擎超时还是品牌真没被提到。

**3. 原始返回必须存。** `responses.raw_json` 存模型原始 JSON。解析规则改了（比如新增一批别名、换情绪词表），`reanalyze` 对着历史回答重跑即可，不用重新花钱采集。

## 四、指标口径

分母只算 `status = 'success'` 的回答。超时、拒答、报错全部排除，否则引擎一抖动指标就掉，分不清是引擎的问题还是品牌的问题。

| 指标 | 公式 | 方向 |
|---|---|---|
| 提及率 | 提到该品牌的回答数 / 样本数 | 越高越好 |
| 首位提及率 | 该品牌位次为 1 的回答数 / 样本数 | 越高越好 |
| 平均位次 | 被提及时的位次均值（1 最好） | **越低越好** |
| 声量份额 SoV | 该品牌提及次数 / 所有品牌提及次数 | 越高越好 |
| 引用率 | 引用自家域名的回答数 / 样本数 | 越高越好 |
| 情感分 | 正面提及数 / 该品牌提及数 × 100 | 越高越好 |

位次的定义：在同一段回答里，谁的名字**先出现**谁排前面。同一下标时自家优先。所以「平均位次 2.4」的意思是：品牌被提到时，平均排在第二个被提及的位置。

两条容易错的地方：

- **SoV 的分母是提及次数，不是回答数。** 一段回答提了两次同一品牌，算两次。
- **平均位次是反向指标。** 前端上色不能按 `index` 猜，要用视图返回的 `is_good`。这条在 `04` 里写过，视图已经算好了。

## 五、加一家新引擎

以 Kimi 为例，三步，`collector` / `metrics` / 视图都不用动：

1. `app/core/config.py` 加三个字段：`kimi_api_key` / `kimi_base_url` / `kimi_model`
2. `app/engines/registry.py` 的 `_configs()` 里加一条映射，`_PRICING` 里加单价
3. `.env.example` 补三行

如果某家的协议不兼容 OpenAI（比如要求特殊字段），在 `app/engines/` 下新建 `kimi.py`，实现 `EngineClient` 协议并导出 `build(config)`。注册表会自动优先使用它。

## 六、定时采集

现在没接调度器，靠命令行手动跑。上线时用 systemd timer 每天跑一次：

```ini
# /etc/systemd/system/geo-collect.service
[Unit]
Description=GEO 每日采集
After=network-online.target

[Service]
Type=oneshot
User=deploy
WorkingDirectory=/srv/geo/backend
EnvironmentFile=/srv/geo/backend/.env
ExecStart=/srv/geo/backend/.venv/bin/python -m app.cli collect --project 演示项目 --metrics
```

```ini
# geo-collect.timer
[Unit]
Description=每天 03:00 采集

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

`Persistent=true` 很重要：服务器宕机错过的任务，开机后会补跑一次。

## 七、已知限制（v0.1）

| 限制 | 影响 | 打算 |
|---|---|---|
| 只算全引擎汇总，不按引擎分开存 | 看板够用，但看不到「DeepSeek 和豆包哪个对品牌更友好」 | `daily_metrics` 已留 `engine` 列，`recompute` 支持按引擎算，加个循环即可 |
| 情绪靠词表判定 | 「不算差」会被判成负面 | `mentions.verified_by` 留了 `llm` 位，后期接一层大模型复核 |
| 别名匹配没有消歧 | 品牌名撞上通用词会误判（如「小红书」撞上书名） | 已存 `matched_term`，可回溯误判来源；后续加白名单上下文规则 |
| 引用域名靠正则 + 关键词表 | 回答只写「某乎」不写链接时抓不到 | 关键词表可持续补 |
| 没有重试队列 | 单条失败要等下一轮 | 失败样本已进库，可写个「只重跑失败项」的命令 |
| 没有成本上限 | 问题库大了会烧钱 | 已有 `COLLECT_MAX_PROMPTS` 兜底，后续加每日预算检查 |

## 八、测试

```bash
python -m pytest          # 26 个用例
python -m ruff check .    # 规范
```

- `tests/test_analyzer.py`：品牌命中、别名、英文边界（`RED` 不能命中 `CREDIT`）、位次、情绪、域名抽取
- `tests/test_metrics.py`：用内存 SQLite 跑真实聚合，验证「失败样本不进分母」「SoV 用提及次数」「重算幂等」「一个都没提到也要写 0 行」

聚合这块一定要有测试：手算六个指标很容易错，而看板上的数字错了没人能一眼看出来。
