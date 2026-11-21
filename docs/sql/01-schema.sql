-- =============================================================================
-- GEO 平台 · 数据表与视图
-- 版本 v0.1 / 2026-11-20
--
-- 执行方式：Supabase Dashboard -> SQL Editor -> 整段粘贴 -> Run
-- 顺序：先执行本文件，再执行 02-auth.sql
-- 幂等：全部语句可重复执行（create ... if not exists / create or replace）
--
-- 说明：内容与 docs/04-supabase-contract.md 第 3、4 节一致，
--       04 里第 5 节那套 anon 授权已被 02-auth.sql 取代，此处不收录。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 一、六张业务表
-- -----------------------------------------------------------------------------

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
  -- NULLS NOT DISTINCT 必须带上（PG 15+）：普通 UNIQUE 认为两个 NULL 互不相等，
  -- engine is null 的"全引擎汇总"行就能重复插入，看板数字会漂移。
  constraint daily_metrics_unique
    unique nulls not distinct (project_id, metric_date, engine, subject)
);

-- -----------------------------------------------------------------------------
-- 二、索引
-- -----------------------------------------------------------------------------

create index if not exists idx_prompts_project      on prompts(project_id) where enabled;
create index if not exists idx_runs_project_time    on runs(project_id, created_at desc);
create index if not exists idx_responses_run        on responses(run_id);
create index if not exists idx_responses_prompt     on responses(prompt_id);
create index if not exists idx_mentions_response    on mentions(response_id);
create index if not exists idx_mentions_brand       on mentions(brand);
create index if not exists idx_daily_lookup         on daily_metrics(project_id, engine, subject, metric_date desc);

-- -----------------------------------------------------------------------------
-- 三、四个看板视图
--     只读，前端通过它们拿数据；写入全部由采集器（service_role）完成
-- -----------------------------------------------------------------------------

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

-- 信源分布：按"回答"计数（同一回答内重复域名只算一次），窗口取最近 7 天。
-- 已执行过旧版的环境用 03-fixes.sql 覆盖，不要重跑本文件。
create or replace view v_source_distribution as
with latest as (
  select project_id, max(metric_date) as as_of
  from daily_metrics where engine is null group by project_id
),
pairs as (
  select distinct
    u.project_id, src.domain, r.id as response_id, r.called_at
  from responses r
  join runs u on u.id = r.run_id
  left join latest l on l.project_id = u.project_id
  cross join lateral jsonb_array_elements_text(coalesce(r.cited_domains, '[]'::jsonb)) as src(domain)
  where r.status = 'success'
    and r.called_at >= coalesce(l.as_of, (now() at time zone 'utc')::date) - interval '6 days'
    and r.called_at <  coalesce(l.as_of, (now() at time zone 'utc')::date) + interval '1 day'
),
per_domain as (
  select project_id, domain, count(*) as citation_count, max(called_at) as last_seen_at
  from pairs group by project_id, domain
)
select project_id, domain as name, citation_count,
       round(100.0 * citation_count / sum(citation_count) over (partition by project_id), 1) as value,
       last_seen_at
from per_domain;

-- 样本量取最新一天，不累加历史（累加会把 7 天说成 1 天）
create or replace view v_sample_summary as
select
  d.project_id,
  d.metric_date         as as_of,
  d.sample_size::bigint as samples
from daily_metrics d
where d.engine is null
  and d.is_own
  and d.metric_date = (
    select max(x.metric_date) from daily_metrics x
    where x.project_id = d.project_id and x.engine is null and x.is_own
  );

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

-- 视图权限与 security_invoker 在 02-auth.sql 第 7 节统一处理
