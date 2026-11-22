# 后端采集与分析服务 v0.1

数据层是 Supabase 直连，所以「后端」不是一套 REST 接口，而是三件事：**去各家引擎问问题 → 判断回答里提到了谁 → 算成每天的指标**。这份文档说明代码怎么组织、怎么跑、怎么扩。

## 一、代码结构

```
app/
├─ api/                   给前端和运维用的薄接口层（鉴权 + 触发采集 + 探针，见 docs/08）
├─ cli.py                 命令行入口（init / check / verify / engines / seed / prompts / collect / metrics / stats / reanalyze）
├─ core/
│  ├─ config.py           环境变量（数据库串、各引擎密钥、并发与超时、报告时区）
│  ├─ db.py               异步 engine + session 工厂 + 连接串归一化（sslmode → asyncpg ssl）
│  ├─ clock.py            业务时区：按 Asia/Shanghai 切天，而不是 UTC
│  ├─ security.py         校验 Supabase 签发的 JWT
│  └─ logging.py          结构化日志（沿用框架那套，带 request_id）
├─ models/
│  ├─ base.py             声明式基类 + JSON 类型（PG 用 jsonb，SQLite 退化为 JSON）
│  ├─ geo.py              6 张表：projects / prompts / runs / responses / mentions / daily_metrics
│  └─ auth.py             profiles / project_members（读 Supabase Auth 建的表）
├─ engines/
│  ├─ base.py             EngineClient 协议 + EngineAnswer 统一结果
│  ├─ openai_compat.py    OpenAI 兼容实现（DeepSeek / 通义 / 豆包共用）
│  └─ registry.py         引擎注册表：密钥、模型、计费单价
└─ services/
   ├─ collector.py        run_once：并发调引擎 → 串行落库 → 解析提及 → 自动算指标
   ├─ analyzer.py         纯函数：品牌命中、位次、情绪、引用域名
   ├─ metrics.py          聚合：responses + mentions → daily_metrics
   ├─ membership.py       项目角色查询（权限判定唯一入口）
   ├─ schema_check.py     线上 schema 体检（只读系统表，可随时在生产跑）
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

# 3.5 线上 schema 体检：视图 security_invoker、唯一约束、只读策略、视图列
python -m app.cli verify

# 4. 真采集（需先在 .env 配好 DEEPSEEK_API_KEY）
python -m app.cli prompts --project 演示项目 --file qs.txt    # 批量灌问题库
python -m app.cli collect --project 演示项目 --limit 2 --dry-run   # 先试跑不写库
python -m app.cli collect --project 演示项目                       # 采集，结束自动算指标
python -m app.cli collect --all                                    # 所有项目各跑一轮（定时任务用）

# 5. 看用量和成本
python -m app.cli stats --project 演示项目 --days 7

# 6. 规则改了，重跑解析（不重新调引擎，不花钱）
python -m app.cli reanalyze --run <run_id>
```

线上把 `DATABASE_URL` 换成 Supabase 的连接串即可，代码不用改。**线上建表用 `docs/sql/01-schema.sql`，不要用 `cli init`**——后者只建表，没有视图、没有 RLS。

### 连接串怎么填

优先用 **Session pooler**，不要去连 `db.<ref>.supabase.co`：那个域名很多机器只有 IPv6 记录，会直接 `ENETUNREACH`。

```
DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<密码>@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres
```

`?sslmode=require` 加不加都行——`app/core/db.py` 会把它翻译成 asyncpg 认识的 `ssl` 参数。**必须翻译的原因是**：asyncpg 不认 libpq 的 `sslmode`，原样传过去会被当成会话参数发给服务端，报 `unrecognized configuration parameter "sslmode"`，而且报错位置在看不出关系的地方。

连接池默认 3 + 2：Session pooler 的连接数有配额，池子开大了会挤掉别的客户端。

## 三、三个刻意的设计

**1. HTTP 并发，写库串行。** 20 个问题 × 3 家引擎串行要十几分钟；并发抓完再按顺序写只要一两分钟。之所以不边抓边写：同一个 `AsyncSession` 不能被多个协程同时使用，会直接报错。**客户端按引擎复用**（一家一个 `AsyncClient`），不是每个问题新建一个——后者 60 个回答要做 60 次 TLS 握手。

**2. 失败不中断，失败也进库。** 单条失败只写一条 `status != 'success'` 的 `responses`，整轮 `run.status` 记 `partial`。这样「今天指标为什么掉了」能查到是引擎超时还是品牌真没被提到。重试只针对超时、429、5xx；400/401/404 这类配置错误立即失败——重试一万次也一样，白等 30s + 120s 还占着并发位。

**3. 原始返回必须存。** `responses.raw_json` 存模型原始 JSON。解析规则改了（比如新增一批别名、换情绪词表），`reanalyze` 对着历史回答重跑即可，不用重新花钱采集。

## 四、指标口径

分母只算 `status = 'success'` 的回答。超时、拒答、报错全部排除，否则引擎一抖动指标就掉，分不清是引擎的问题还是品牌的问题。

**日期按 `REPORT_TIMEZONE` 切天（默认 `Asia/Shanghai`），不是 UTC。** `responses.called_at` 存的是 UTC，但 `daily_metrics.metric_date` 是业务日：北京时间 22 日凌晨 1 点采集的数据算 22 日，不是 21 日。实现上不用 `date(called_at) = '...'`，而是用 `[当天00:00, 次日00:00)` 的 UTC 区间比较——函数套在列上会让索引失效，而且 SQLite 与 Postgres 的 `date()` 语义并不完全一致。

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

单元文件在 `deploy/` 下，直接拷到 `/etc/systemd/system/`，安装步骤见 `deploy/README.md`，完整部署流程见 `docs/02-deploy.md`。

调度入口是 `collect --all`：逐个跑所有项目，单个项目失败不影响其他项目，最后用退出码告诉 systemd 这轮有没有失败。手动跑一轮：

```bash
sudo systemctl start geo-collect.service
journalctl -u geo-collect -n 80 --no-pager
```

两个设计要点：

- **不用 API 进程内定时器。** API 会被重启（发版、OOM），进程里的定时任务跟着没；systemd timer 是系统级的，`Persistent=true` 还能补跑宕机错过的点。
- **`EnvironmentFile` 不要写。** 配置由 pydantic-settings 从 `WorkingDirectory/.env` 读，走 systemd 的 `EnvironmentFile` 会被它的引号规则坑到（密码里的 `#`、`$` 会被吃掉）。

## 七、已知限制（v0.1）

| 限制 | 影响 | 打算 |
|---|---|---|
| 只算全引擎汇总，不按引擎分开存 | 看板够用，但看不到「DeepSeek 和豆包哪个对品牌更友好」 | `daily_metrics` 已留 `engine` 列，`recompute` 支持按引擎算，加个循环即可 |
| 情绪靠词表判定 | 「不算差」会被判成负面 | `mentions.verified_by` 留了 `llm` 位，后期接一层大模型复核 |
| 别名匹配没有消歧 | 品牌名撞上通用词会误判（如「小红书」撞上书名） | 已存 `matched_term`，可回溯误判来源；后续加白名单上下文规则 |
| 引用域名靠正则 + 关键词表 | 回答只写「某乎」不写链接时抓不到 | 关键词表可持续补 |
| 没有重试队列 | 单条失败要等下一轮 | 失败样本已进库，可写个「只重跑失败项」的命令 |
| 没有成本上限 | 问题库大了会烧钱 | 已有 `COLLECT_MAX_PROMPTS` 兜底，`cli stats` 能看每日花费，后续加预算检查 |
| 并发触发靠数据库状态判定 | 多 worker 部署时仍可能有极短窗口 | 单实例 + systemd timer 的部署方式下够用；上多实例时改用任务队列 |
| 时区只支持全局一个 | 多地区客户不能各自切天 | `report_timezone` 现在是进程级配置；多租户需要把它挪到 `projects` 表 |

## 八、测试

```bash
python -m pytest          # 70 个用例
python -m ruff check .    # 规范
```

- `tests/test_analyzer.py`：品牌命中、别名、英文边界（`RED` 不能命中 `CREDIT`）、位次、情绪、域名抽取
- `tests/test_metrics.py`：用内存 SQLite 跑真实聚合，验证「失败样本不进分母」「SoV 用提及次数」「重算幂等」「一个都没提到也要写 0 行」
- `tests/test_collector.py`：假引擎 + 真数据库，走完「回答落库 → 提及解析 → 指标重算」，验证采完看板就有数、`run_id` 复用不会重复建批次、全引擎故障时批次标 failed
- `tests/test_clock.py`：业务时区的切天边界（北京 03:00 算哪一天、时区配错退回 UTC）
- `tests/test_db_url.py`：连接串归一化（`sslmode` 翻译、驱动补全、密码里的 `@` 不被拆坏）
- `tests/test_engine_retry.py`：400/401 不重试、429 重试后成功、5xx 用尽次数、成本计算
- `tests/test_auth.py`：JWT 的各种坏情况 + 角色门禁

聚合这块一定要有测试：手算六个指标很容易错，而看板上的数字错了没人能一眼看出来。
