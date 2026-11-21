# GEO 看板数据契约 v0.1

> 适用方案：Supabase 直连（数据库 + 鉴权交给 Supabase，后端只做采集与计算）
> 面向：前端（Next.js 16 + React 19）与后端（Python 采集器）
> 状态：待前端确认，确认后双方按此开发，字段不再单方面改动

---

## 0. 这份文档是什么，不是什么

**是**：前端要读哪几张表/视图、每个字段叫什么、什么类型、什么含义；后端往哪里写、什么时候写；谁有权限读写。

**不是**：REST 接口文档。本方案下前端直接调 `supabase.from(...)`，没有 HTTP 接口层，所以"接口契约"就退化成"数据契约"。

这一点必须先讲清楚，因为它决定了出问题时的排查路径：**没有后端日志可以看，只能查数据库。** 任何"数据不对"的问题，第一反应是去 Supabase 的 Table Editor 看表里到底是什么，而不是找后端要日志。

---

## 1. 方案的已知取舍（选择了就要接受）

| 拿到的 | 付出的 |
|---|---|
| 前后端不用联调，前端自己就能增删改查 | **没有服务端校验**。谁能写、写什么，全靠数据库约束和 RLS，业务规则写不进代码 |
| 不用维护 API 文档、不用管跨域、不用管部署 | 采集和分析逻辑只能在服务器上跑，前端一点忙帮不上 |
| Supabase 自带登录、存储、实时订阅 | 数据在第三方云上，**私有化部署这条路直接断了**（基线里"私有化预留"作废） |
| 免费额度起步（500MB 库、5GB 流量） | 免费项目 7 天无活动自动暂停；国内访问 Supabase 域名不稳定，前端在 Vercel 则更慢 |
| 开发速度最快 | 后续想换成自建后端，要重写前端所有数据调用（约 1 天） |

三条硬约束，越早接受越少返工：

1. **`service_role` key 绝对不能出现在前端代码或任何会被浏览器下载的文件里。** 它绕过所有 RLS，泄露等于数据库裸奔。它只存在于你服务器的环境变量中。
2. **前端只用 `anon` key，且只读。** 本方案里前端不需要写任何业务数据。
3. **指标是算出来的，不是填进去的。** 现有"新增指标"弹窗在这个模型下没有对应物，必须删掉（见第 7 节）。

---

## 2. 现有三张表的问题

| 表 | 现状 | 问题 |
|---|---|---|
| `metrics` | `label / value / trend / footnote` 四个 text | 存的是 `"12.5%"`、`"↑ 2.1pp"` 这种**展示字符串**。数据库里没有数字，无法排序、无法环比、无法按时间筛选、无法换语言。而且这张表靠人手工填 |
| `source_distribution` | `name / value / color` | `color` 是表现层的东西，不该进库。前端换主题、换配色方案就得改数据 |
| `radar_metrics` | `subject / a / b` | `a` / `b` 无语义，加第三个竞品就得改表结构。竞品名还写死在 `RadarChart.tsx` 里 |
| 三张表共同 | 都自带 `project_id`？没有 | **没有项目概念**。一个库只能存一个品牌的数据，第二个客户进来就冲突 |

另外 `page.tsx` 里按数组下标分配颜色：

```tsx
valueColor={index === 0 ? "text-rose-600" : index === 1 ? "text-emerald-600" : ...}
```

这意味着卡片顺序一变，红色绿色全错位。而且"红色=坏"这个假设对"平均位次"是**反的**——位次从 3.2 降到 2.1 是变好，但数值是下降的。所以视图里直接给 `direction` 和 `is_good` 两个字段，前端别猜。

---

## 3. 目标数据模型

六张业务表 + 三张视图。业务表只由后端采集器写，视图给前端读。

```
projects ──┬── prompts
           └── runs ──── responses ──── mentions
                  │
                  └── daily_metrics（按天/按品牌聚合，看板读这个的视图）
```

### 3.1 六张业务表

在 Supabase 的 SQL Editor 里整段执行。`gen_random_uuid()` 是 PG13+ 内置，Supabase 默认可用。

```sql
-- 项目（一个客户/一个品牌 = 一个 project）
create table if not exists projects (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  brand        text not null,
  aliases      jsonb not null default '[]'::jsonb,   -- ["小红书","RED"]
  competitors  jsonb not null default '[]'::jsonb,   -- ["抖音","快手"]
  created_at   timestamptz not null default now()
);

-- 监测问题库
create table if not exists prompts (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references projects(id) on delete cascade,
  text        text not null,
  category    text,
  enabled     boolean not null default true,
  created_at  timestamptz not null default now()
);

-- 一次采集任务
create table if not exists runs (
  id           uuid primary key default gen_random_uuid(),
  project_id   uuid not null references projects(id) on delete cascade,
  status       text not null default 'pending'
               check (status in ('pending','running','success','partial','failed')),
  prompt_count int  not null default 0,
  engine_count int  not null default 0,
  error        text,
  started_at   timestamptz,
  finished_at  timestamptz,
  created_at   timestamptz not null default now()
);

-- 单次回答（一个 run × 一个问题 × 一个引擎 = 一条）
create table if not exists responses (
  id             uuid primary key default gen_random_uuid(),
  run_id         uuid not null references runs(id) on delete cascade,
  prompt_id      uuid not null references prompts(id) on delete cascade,
  engine         text not null,              -- deepseek / qwen / doubao
  model          text,
  question       text not null,              -- 冗余存一份，问题改写历史后原文不变
  answer_text    text,
  raw_json       jsonb,                      -- 原始返回，解析规则改了能重跑
  cited_domains  jsonb not null default '[]'::jsonb,  -- ["zhihu.com","xiaohongshu.com"]
  status         text not null default 'success'
                 check (status in ('success','empty','refused','timeout','error','blocked')),
  tokens_in      int,
  tokens_out     int,
  cost_usd       numeric(10,6),
  called_at      timestamptz not null default now()
);

-- 回答里识别出的品牌提及（一个回答可能提到多个品牌）
create table if not exists mentions (
  id           uuid primary key default gen_random_uuid(),
  response_id  uuid not null references responses(id) on delete cascade,
  brand        text not null,
  is_own       boolean not null default false,
  position     int,                          -- 第几个被提到，1 开始；null = 提到了但没排位
  sentiment    text check (sentiment in ('positive','neutral','negative')),
  matched_term text,                         -- 命中的别名，便于回溯误判
  verified_by  text not null default 'rule'  -- rule / llm / human
               check (verified_by in ('rule','llm','human')),
  created_at   timestamptz not null default now()
);

-- 每日聚合（看板的唯一数据源）
create table if not exists daily_metrics (
  id                  uuid primary key default gen_random_uuid(),
  project_id          uuid not null references projects(id) on delete cascade,
  metric_date         date not null,
  engine              text,                  -- null = 全部引擎汇总；否则单引擎
  subject             text not null,         -- 品牌名，自家和竞品都在这里
  is_own              boolean not null default false,
  sample_size         int not null default 0,-- 进分母的回答数
  mention_rate        numeric(6,4),          -- 0.4231 = 42.31%
  first_mention_rate  numeric(6,4),
  avg_position        numeric(6,2),
  sov                 numeric(6,4),          -- 声量份额
  citation_rate       numeric(6,4),          -- 引用了自家域名的回答占比
  sentiment_score     numeric(5,2),          -- 0-100，正面占比
  calc_version        text not null default 'v0.1',
  computed_at         timestamptz not null default now(),
  unique (project_id, metric_date, engine, subject)
);
```

索引（采集器查询和看板视图都要用）：

```sql
create index if not exists idx_prompts_project      on prompts(project_id) where enabled;
create index if not exists idx_runs_project_time    on runs(project_id, created_at desc);
create index if not exists idx_responses_run        on responses(run_id);
create index if not exists idx_responses_prompt     on responses(prompt_id);
create index if not exists idx_mentions_response    on mentions(response_id);
create index if not exists idx_mentions_brand       on mentions(brand);
create index if not exists idx_daily_lookup         on daily_metrics(project_id, engine, subject, metric_date desc);
```

### 3.2 字段口径（与 `docs/01`、需求基线第 13 章一致）

| 字段 | 定义 | 边界处理 |
|---|---|---|
| `sample_size` | 该天该品牌所在项目的**有效回答数** | 只算 `status = 'success'`；`empty/refused/timeout/error/blocked` 不进分母 |
| `mention_rate` | 提到该品牌的回答数 / `sample_size` | 无人提及记 `0`，不是 `null` |
| `first_mention_rate` | 该品牌是**第一个**被提到的回答数 / `sample_size` | 没有位次信息的记为未命中 |
| `avg_position` | 命中 `mentions.position` 的均值 | 全未命中记 `null`（前端显示 `—`，不显示 0，否则会被误读成"排第一"） |
| `sov` | 该品牌提及次数 / 全部品牌提及次数 | 项目无竞品时等于 1.0 |
| `citation_rate` | `cited_domains` 含自有域名的回答数 / `sample_size` | 自有域名列表从 `projects` 配置读 |
| `sentiment_score` | `positive` 提及数 / 全部提及数 × 100 | 无提及记 `null` |

**所有比率字段存 0–1 的小数，不存百分数。** 展示时乘 100 由前端做。这样聚合时可以直接 `avg()`，存成 `42.31` 的话求平均会算错。

---

## 4. 三个看板视图

前端只读这三个视图，不碰业务表。视图已算好展示文案和方向，前端不再做判断。

### 4.1 `v_metric_cards` — 顶部四个指标卡

```sql
create or replace view v_metric_cards as
with ranked as (
  select dm.*,
         row_number() over (partition by dm.project_id, dm.subject
                            order by dm.metric_date desc) as rn
  from daily_metrics dm
  where dm.engine is null and dm.is_own
),
cur  as (select * from ranked where rn = 1),
prev as (select * from ranked where rn = 2)
select
  c.project_id,
  k.metric_key,
  k.label,
  k.val                                                        as value_num,
  case k.unit
    when 'pct' then coalesce(to_char(k.val * 100, 'FM990.0') || '%', '—')
    when 'pos' then coalesce(to_char(k.val, 'FM990.0') || ' 位', '—')
    else to_char(k.val, 'FM999,999')
  end                                                          as value_text,
  k.prev                                                       as prev_num,
  case
    when k.prev is null then null
    when k.unit = 'pct' then to_char((k.val - k.prev) * 100, 'FM990.0') || 'pp'
    else to_char(k.val - k.prev, 'FM990.0')
  end                                                          as delta_text,
  case
    when k.prev is null then 'flat'
    when k.val > k.prev then 'up'
    when k.val < k.prev then 'down'
    else 'flat'
  end                                                          as direction,
  case
    when k.prev is null or k.val = k.prev then null
    else (k.val > k.prev) = k.higher_is_better
  end                                                          as is_good,
  c.metric_date                                                as as_of,
  c.sample_size                                                as sample_size
from cur c
left join prev p
  on p.project_id = c.project_id and p.subject = c.subject
cross join lateral (values
  ('mention_rate',       '品牌提及率', c.mention_rate,       p.mention_rate,       'pct', true),
  ('first_mention_rate', '首位提及率', c.first_mention_rate, p.first_mention_rate, 'pct', true),
  ('avg_position',       '平均位次',   c.avg_position,       p.avg_position,       'pos', false),
  ('sov',                '声量份额',   c.sov,                p.sov,                'pct', true)
) as k(metric_key, label, val, prev, unit, higher_is_better);
```

返回（以某个项目为例）：

```jsonc
[
  { "project_id": "…", "metric_key": "mention_rate", "label": "品牌提及率",
    "value_num": 0.4231, "value_text": "42.3%", "prev_num": 0.4020,
    "delta_text": "2.1pp", "direction": "up", "is_good": true,
    "as_of": "2026-11-19", "sample_size": 40 },

  { "project_id": "…", "metric_key": "avg_position", "label": "平均位次",
    "value_num": 2.10, "value_text": "2.1 位", "prev_num": 3.20,
    "delta_text": "-1.1", "direction": "down", "is_good": true,
    "as_of": "2026-11-19", "sample_size": 40 }
]
```

**颜色怎么来**：`is_good === true` → 绿；`false` → 红；`null` → 灰。`direction` 只决定箭头朝向。**不要再按数组下标判断。**

### 4.2 `v_source_distribution` — 信源分布环形图

```sql
create or replace view v_source_distribution as
select
  u.project_id,
  src.domain                                                        as name,
  count(*)                                                          as citation_count,
  round(100.0 * count(*) / sum(count(*)) over (partition by u.project_id), 1) as value,
  max(r.called_at)                                                  as last_seen_at
from responses r
join runs u on u.id = r.run_id
cross join lateral jsonb_array_elements_text(coalesce(r.cited_domains, '[]'::jsonb)) as src(domain)
where r.status = 'success'
group by u.project_id, src.domain;
```

返回：

```jsonc
[
  { "project_id": "…", "name": "zhihu.com", "citation_count": 312, "value": 28.4 },
  { "project_id": "…", "name": "xiaohongshu.com", "citation_count": 241, "value": 21.9 }
]
```

两处变化：

- 没有 `color` 字段。前端固定一套调色板，按下标取色（`#2563EB / #059669 / #D97706 / #7C3AED / #64748B`），第 6 个以后循环。
- `value` 是百分比数值（`28.4` 表示 28.4%），Recharts 直接吃，不需要再算。

圆心那个 `1,284` 是写死的，改成从项目维度取：

```sql
create or replace view v_sample_summary as
select project_id,
       max(metric_date) as as_of,
       sum(sample_size) filter (where engine is null and is_own) as samples
from daily_metrics group by project_id;
```

### 4.3 `v_radar_metrics` — 竞品雷达

六维固定，来自需求基线第 13.4 节的评分权重。`own_value` 是自家品牌，`competitor_value` 是全部竞品的均值。

```sql
create or replace view v_radar_metrics as
with latest as (
  select project_id, max(metric_date) as metric_date
  from daily_metrics where engine is null group by project_id
),
cur as (
  select dm.* from daily_metrics dm
  join latest l on l.project_id = dm.project_id and l.metric_date = dm.metric_date
  where dm.engine is null
),
own as (select * from cur where is_own),
cmp as (
  select project_id,
         avg(mention_rate) m, avg(first_mention_rate) f, avg(avg_position) p,
         avg(sov) s, avg(citation_rate) c, avg(sentiment_score) se
  from cur where not is_own group by project_id
)
select
  o.project_id,
  k.subject,
  round(k.own_v::numeric, 1)                                      as own_value,
  round(coalesce(k.cmp_v, 0)::numeric, 1)                         as competitor_value,
  l.metric_date                                                   as as_of
from own o
join latest l on l.project_id = o.project_id
left join cmp c on c.project_id = o.project_id
cross join lateral (values
  ('可见度',   o.mention_rate * 100,       c.m * 100),
  ('首位提及', o.first_mention_rate * 100, c.f * 100),
  ('位次',     coalesce(greatest(0, 110 - o.avg_position * 10), 0),
               coalesce(greatest(0, 110 - c.p           * 10), 0)),
  ('声量份额', o.sov * 100,                c.s * 100),
  ('引用率',   o.citation_rate * 100,      c.c * 100),
  ('情感',     coalesce(o.sentiment_score, 0), coalesce(c.se, 0))
) as k(subject, own_v, cmp_v);
```

返回 6 行：

```jsonc
[
  { "project_id": "…", "subject": "可见度",   "own_value": 42.3, "competitor_value": 31.7, "as_of": "2026-11-19" },
  { "project_id": "…", "subject": "首位提及", "own_value": 18.0, "competitor_value": 12.4, "as_of": "2026-11-19" },
  { "project_id": "…", "subject": "位次",     "own_value": 89.0, "competitor_value": 76.0, "as_of": "2026-11-19" }
]
```

三点说明：

- 位次要**反向归一化**才能上雷达（位次越小越好，但雷达图越大越好）。这里用 `110 - 位次×10`，位次 1 得 100，位次 10 得 10，位次超过 11 截断到 0。这个系数是暂时的，等真实数据跑出来再校准。
- 没有任何竞品时 `competitor_value` 为 0，雷达只剩一片。前端应在项目没有配竞品时隐藏这张图，而不是画个 0。
- 雷达半径 `domain={[0, 150]}` 现在设的 150，按实际值域应该是 `[0, 100]`。

---

## 5. 权限模型

前端拿的是 `anon` key，必须保证它**只能读三个视图，不能读写业务表**。

```sql
-- 1. 业务表全部关掉 anon 权限
alter table projects       enable row level security;
alter table prompts        enable row level security;
alter table runs           enable row level security;
alter table responses      enable row level security;
alter table mentions       enable row level security;
alter table daily_metrics  enable row level security;

-- 建 RLS 后不加 policy = anon 完全无法访问，只有 service_role 能写
-- 采集器用 service_role，不受 RLS 限制

-- 2. 视图只给读
grant select on v_metric_cards, v_source_distribution, v_radar_metrics, v_sample_summary to anon;

-- 3. 还没做登录时，视图对所有人可读 = 任何人知道 URL 就能看到数据
--    上线前必须改成基于 Supabase Auth 的过滤（见下方说明）
```

**注意：本节已被 `docs/05-auth-permission.md` 取代。**

本节给的是"还没有登录"的临时版本：视图对 `anon` 公开可读，只要有人知道 Supabase 地址就能看到全部看板数据，只在演示阶段可接受。

实际的权限方案在 `docs/sql/02-auth.sql`，执行后本节 `grant select ... to anon` 会被 `revoke` 掉，改成只授权给 `authenticated`，并且四个视图全部设为 `security_invoker = true`（否则视图会以所有者权限执行，绕过底层表的 RLS）。

**必须现在就确认的一件事**：`service_role` key 生成的当下，就存进服务器环境变量，**不要**复制进微信、不要写进任何 `.ts` 文件、不要贴进聊天记录。如果已经贴过，去 Settings → API 轮换一次。

---

## 6. 采集器契约

采集器是一个独立 Python 服务，跑在你的服务器上，和前端没有任何代码往来。

### 6.1 依赖与连接

```bash
pip install "supabase>=2.18" httpx
```

```python
# collector/db.py
import os
from supabase import create_client

# service_role key，只在服务器环境变量里
sb = create_client(
    os.environ["SUPABASE_URL"],  # https://xxxx.supabase.co
    os.environ["SUPABASE_SERVICE_KEY"],  # sb_secret_... 或 service_role JWT
)
```

### 6.2 一次完整采集的流程

```python
def run_once(project_id: str) -> str:
    # 1. 建任务
    run = (
        sb.table("runs")
        .insert(
            {
                "project_id": project_id,
                "status": "running",
                "started_at": now_iso(),
            }
        )
        .execute()
        .data[0]
    )

    prompts = (
        sb.table("prompts")
        .select("id,text")
        .eq("project_id", project_id)
        .eq("enabled", True)
        .execute()
        .data
    )

    ok = fail = 0
    for p in prompts:
        for engine in ("deepseek", "qwen"):
            try:
                r = call_engine(engine, p["text"])  # 见 6.3
                resp = (
                    sb.table("responses")
                    .insert(
                        {
                            "run_id": run["id"],
                            "prompt_id": p["id"],
                            "engine": engine,
                            "model": r.model,
                            "question": p["text"],
                            "answer_text": r.text,
                            "raw_json": r.raw,
                            "cited_domains": r.domains,
                            "status": r.status,
                            "tokens_in": r.in_tokens,
                            "tokens_out": r.out_tokens,
                        }
                    )
                    .execute()
                    .data[0]
                )
                ok += 1

                # 2. 解析品牌提及
                for m in match_mentions(r.text, project):
                    m["response_id"] = resp["id"]
                    sb.table("mentions").insert(m).execute()
            except Exception as e:
                fail += 1
                sb.table("responses").insert(
                    {
                        "run_id": run["id"],
                        "prompt_id": p["id"],
                        "engine": engine,
                        "question": p["text"],
                        "status": "error",
                        "answer_text": str(e)[:500],
                    }
                ).execute()

    # 3. 收尾
    sb.table("runs").update(
        {
            "status": "success" if fail == 0 else "partial",
            "finished_at": now_iso(),
            "prompt_count": len(prompts),
            "engine_count": 2,
            "error": f"{fail} 次调用失败" if fail else None,
        }
    ).eq("id", run["id"]).execute()

    # 4. 算聚合
    recompute_daily(project_id, date.today())
    return run["id"]
```

### 6.3 解析规则（`match_mentions`）

第一阶段只做规则匹配，零成本、可回溯：

```python
def match_mentions(answer: str, project: dict) -> list[dict]:
    """按别名命中，按首次出现位置排序。"""
    targets = [(project["brand"], True)] + [(c, False) for c in project["competitors"]]
    hits = []
    for name, is_own in targets:
        aliases = project["aliases"] if is_own else [name]
        pos = min((answer.find(a) for a in aliases if a in answer), default=-1)
        if pos >= 0:
            hits.append(
                {
                    "_pos": pos,
                    "brand": name,
                    "is_own": is_own,
                    "matched_term": next(a for a in aliases if a in answer),
                }
            )
    hits.sort(key=lambda x: x["_pos"])
    for i, h in enumerate(hits, 1):
        h["position"] = i
        h.pop("_pos")
    return hits
```

要点：

- `position` 按**全文首次出现位置**排序，不是按提及次数。这决定了"首位提及率"的含义，要跟产品确认一次。
- `matched_term` 必存。误判时能一眼看出是哪个别名撞了别人的品牌名。
- `verified_by = 'rule'`。后期接 LLM 复核，只改这一列，表结构不动。
- 情感第一版全部留 `null`，等接了模型再填。**不要用关键词猜正面负面**，误判率会毁掉整张雷达图。

### 6.4 重算聚合

重算是幂等的：删掉当天该项目的行，重新插入。

```python
def recompute_daily(project_id: str, d: date):
    sb.table("daily_metrics").delete().eq("project_id", project_id).eq(
        "metric_date", d.isoformat()
    ).execute()
    # 按 (engine, 品牌) 分组统计后批量 insert，见 docs/01 的指标口径
```

**为什么要幂等**：解析规则改了要重跑，重复跑不能产生重复行。`daily_metrics` 上的 `unique(project_id, metric_date, engine, subject)` 是最后一道防线。

### 6.5 调度

服务器上 systemd timer 或 cron，每天固定时间跑一次：

```ini
# /etc/systemd/system/geo-collector.timer
[Unit]
Description=GEO 每日采集
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

`Persistent=true` 很重要：服务器重启错过时间点，下次开机立刻补跑。

---

## 7. 前端改造清单

四处改动，半天能做完。改完之后前端**没有任何写操作**，这是本方案成立的前提。

### 7.1 `lib/supabase.ts` — 改成读环境变量

```ts
// 改后
import { createClient } from '@supabase/supabase-js'

const url = process.env.NEXT_PUBLIC_SUPABASE_URL!
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!

export const supabase = createClient(url, key)
```

`.env.local`（**不进 git**）：

```
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_...
```

硬编码不致命（`sb_publishable_` 本来就是公开 key），但换环境就要改源码，Vercel 上没法分 preview / production。

### 7.2 `page.tsx` — 换数据源，删掉新增弹窗

```tsx
// 改前
const { data } = await supabase.from('metrics').select('*').order('id', { ascending: true });

// 改后
const { data } = await supabase
  .from('v_metric_cards')
  .select('*')
  .eq('project_id', projectId)
  .order('metric_key');
```

配套改动：

- **删除**整个「新增指标」弹窗（`isModalOpen`、`formData`、`handleAddSubmit`、以及第 119–150 行的 Modal JSX）。指标是算出来的，这个入口留着只会让人往库里塞假数据。
- 卡片渲染改成读新字段：

```tsx
{metrics.map((m) => (
  <MetricCard
    key={m.metric_key}
    label={m.label}
    value={m.value_text}
    delta={m.delta_text}
    tone={m.is_good === null ? 'neutral' : m.is_good ? 'good' : 'bad'}
    footnote={`样本 ${m.sample_size} · ${m.as_of}`}
  />
))}
```

- `MetricCard` 的 props 从 `valueColor / badgeStyle / bgColor` 三个颜色串改成 `tone`，颜色在组件内部映射。这样颜色只有一个来源。

### 7.3 `SourceChart.tsx` — 换表和调色板

```tsx
// 改后
const { data } = await supabase
  .from('v_source_distribution')
  .select('name,value,citation_count')
  .eq('project_id', projectId)
  .order('value', { ascending: false })
  .limit(6);
```

- 删掉 `color === '#E2E8F0'` 的暗色补丁逻辑，颜色改由组件内固定调色板按 index 分配。
- 圆心 `1,284` 改成 `v_sample_summary.samples`。

### 7.4 `RadarChart.tsx` — 换数据形状，删掉硬编码竞品

```tsx
// 改后
const { data } = await supabase
  .from('v_radar_metrics')
  .select('subject,own_value,competitor_value')
  .eq('project_id', projectId);
```

```tsx
<Radar name="本品牌"     dataKey="own_value"        … />
<Radar name="竞品均值"   dataKey="competitor_value" … />
<PolarRadiusAxis domain={[0, 100]} … />
```

- 图例文案从 `projects.brand` 和"竞品均值"读，**不再写死"智标枪""广联达"**。
- `domain` 从 `[0, 150]` 改 `[0, 100]`。

### 7.5 还能顺手做掉的两件小事

- `components/Header.tsx` 内容还是"这是一个测试导航栏"，且没被任何地方引用 → 删掉，或换成真的导航。
- `app/layout.tsx` 的 `metadata` 还是 `Create Next App` → 改标题和 `lang="zh-CN"`。

---

## 8. 迁移步骤

他本地那三张表（`metrics` / `source_distribution` / `radar_metrics`）不要直接 drop，先改名留档：

```sql
alter table metrics              rename to zz_legacy_metrics;
alter table source_distribution  rename to zz_legacy_source_distribution;
alter table radar_metrics        rename to zz_legacy_radar_metrics;
```

然后按顺序：

1. 执行第 3.1 的建表 SQL
2. 执行第 4 节的四个视图
3. 执行第 5 节的 RLS 与 grant
4. **造一份可演示的数据**：手写 1 个 project + 5 个 prompt + 30 条 responses + 对应 mentions，跑一次 `recompute_daily`，看板就有数了。这一步别等真采集
5. 前端按第 7 节改完，关掉旧表引用
6. 确认看板正常后，删掉 `zz_legacy_*`

第 4 步是**解阻塞的关键**：真接口没打通之前，用一份固定的 seed 数据让前端把页面调完，两边不用互相等。这份 seed 我可以在后端仓库出个脚本。

---

## 9. 待确认项

| # | 问题 | 建议 |
|---|---|---|
| Q1 | 首位提及按"全文首次出现"还是"推荐列表第一项"？ | 先用全文首次出现，规则简单可解释 |
| Q2 | 信源分布的分母是"引用次数"还是"回答数"？ | 引用次数（一个回答引 3 个源就记 3 次） |
| Q3 | 雷达六维的权重（25/20/20/15/10/10）现在要不要用来算综合分？ | 先只用雷达展示六维，不算总分；总分等数据量够了再加 |
| Q4 | Supabase 免费版 7 天不活跃会暂停，采集是每天跑的所以不会暂停，但**数据库 500MB 上限**要注意：`responses.raw_json` 最占空间 | 先设保留 90 天，超了归档到对象存储 |
| Q5 | 前端部署在 Vercel，国内访问慢 | 绑自定义域名 + 国内 CDN，或直接挂到你的服务器（`docs/02-deploy.md` 那套 Nginx 配法仍然适用，只是不再反代 API） |
| Q6 | `service_role` key 是否已被之前那次生成泄露 | 建议直接轮换一次，成本为零 |

---

## 10. 对已有文档的影响

| 文档 | 影响 |
|---|---|
| `docs/01-week1-plan.md` | 7 个 REST 接口那节作废，替换成本文档第 4 节的视图。表结构第 3 节那三张表保留（`users` 由 Supabase Auth 接管，`projects`/`prompts` 与本文档一致） |
| `docs/02-deploy.md` | Postgres 安装、FastAPI systemd unit、`/api` 反代三节作废；Nginx 托管前端、HTTPS、防火墙、备份仍然有效。采集器需要一节新的 systemd timer 配置（本文档 6.5 已给） |
| `docs/03-github.md` | 后端仓从"API 服务"变成"采集服务"，CI 保留；Supabase 相关 Secret 加两个：`SUPABASE_URL`、`SUPABASE_SERVICE_KEY` |
| 需求基线 V1.1 第 12 章 | M1 的"FastAPI + PostgreSQL"要改成"Supabase + 采集服务"；第 12.4 私有化预留一条要标为**已放弃** |

这三处我还没动，等这次的契约你确认后一起改。

---

## 附：给前端的消息（可直接复制）

> 你的界面我看了，卡片层次和图表选型都没问题，暗色模式也做对了，UI 这块保留。
>
> 有两件事需要改，改完就能接真数据：
>
> 1. **数据源换成视图**：我把三张表改成了六张业务表 + 三个视图，字段名基本没变（`value` 从 `"42.3%"` 字符串变成 `42.3` 数字，`a`/`b` 变成 `own_value`/`competitor_value`）。具体看 `docs/04-supabase-contract.md` 第 4 节。
> 2. **删掉「新增指标」弹窗**：指标是由采集结果算出来的，不是手填的。这个入口留着，会有人往库里塞假数据，看板数字就不可信了。
>
> 另外三个小改动：
> - 卡片颜色别再按 `index` 分配，视图给了 `direction` 和 `is_good`，按 `is_good` 上色（平均位次是"越小越好"，按下标上色会反过来）
> - 环形图圆心那个 `1,284` 换成 `v_sample_summary.samples`
> - 雷达图里"智标枪 vs 广联达"改成从 `projects` 读，`domain` 从 `[0,150]` 改 `[0,100]`
>
> 还有：`sb_publishable_` 那个 key 没问题，本来就是给浏览器用的。但**如果生成过 service_role 或 secret key，别发给任何人，也别写进代码** —— 那个 key 能绕过所有权限。
>
> 你本地那三张旧表先别删，改名成 `zz_legacy_*` 留着，我这边 seed 数据造好就能联调。
