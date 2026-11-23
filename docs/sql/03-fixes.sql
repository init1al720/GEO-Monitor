-- =============================================================================
-- 03-fixes.sql —— 线上实测后的三处修正 + 清理历史策略
--
-- 执行时机：必须在 00 / 01 / 02 之后。可重复执行（幂等）。
-- 适用环境：PostgreSQL 15 及以上（Supabase 现为 17.x）。
--           本地 SQLite 不要执行本文件，本地建表走 `python -m app.cli init`。
--
-- 修的是什么（对应联调反馈）：
--   1) daily_metrics 的唯一约束对 engine is null 不生效，聚合行能重复插入
--   2) v_sample_summary 把历史各天的样本量累加了，as_of 却是最新一天
--   3) v_source_distribution 单条回答内重复域名被多次计数，且没有时间窗口
--   4) 三张旧演示表上残留 Supabase 表编辑器生成的 Allow public ... 策略
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- 1. 唯一约束：让 NULL 也参与唯一性判断
--    原约束 unique (project_id, metric_date, engine, subject) 在 PG 里
--    NULL 互不相等，所以 engine is null 的"全引擎汇总"行可以插任意多条，
--    视图会随机取一条，看板数字漂移。
--    PG 15+ 的 NULLS NOT DISTINCT 才是我们要的语义。
-- -----------------------------------------------------------------------------
alter table public.daily_metrics
  drop constraint if exists daily_metrics_project_id_metric_date_engine_subject_key;

-- 加约束前先清掉可能已经存在的重复行（同键保留 ctid 最小的一条）。
-- 目前业务表是空的，这一步是给"已经跑过几轮采集"的环境兜底。
delete from public.daily_metrics a
using public.daily_metrics b
where a.ctid > b.ctid
  and a.project_id = b.project_id
  and a.metric_date = b.metric_date
  and a.subject = b.subject
  and a.engine is not distinct from b.engine;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conrelid = 'public.daily_metrics'::regclass
      and conname  = 'daily_metrics_unique'
  ) then
    alter table public.daily_metrics
      add constraint daily_metrics_unique
      unique nulls not distinct (project_id, metric_date, engine, subject);
  end if;
end
$$;

-- -----------------------------------------------------------------------------
-- 2. 样本量：取最新一天，不再累加历史
--    "当前样本量"应该是最近一个采集日的回答数；累加会把 7 天数据说成 1 天。
--    只从 is_own 的行取日期，避免某天只有竞品数据时 as_of 指到没有自家样本的那天。
-- -----------------------------------------------------------------------------
create or replace view public.v_sample_summary as
select
  d.project_id,
  d.metric_date            as as_of,
  d.sample_size::bigint    as samples
from public.daily_metrics d
where d.engine is null
  and d.is_own
  and d.metric_date = (
    select max(x.metric_date)
    from public.daily_metrics x
    where x.project_id = d.project_id
      and x.engine is null
      and x.is_own
  );

-- 视图必须跟着底层表走 RLS，不能用 definer 身份绕过权限
alter view public.v_sample_summary set (security_invoker = true);

-- -----------------------------------------------------------------------------
-- 3. 信源分布：按"回答"计数 + 最近 7 天窗口
--
--    口径定为：一条回答引用同一个域名多次，只算一次；不同回答分别累加。
--    所以 citation_count 的含义是"引用了该域名的回答数"，
--    value 是它在全部信源引用里的占比，分母为该项目窗口内的总引用次数。
--    （不按原始出现次数计，是因为模型常在正文和参考列表里重复写同一链接，
--      按次数算会把单个高重复域名放大。）
--
--    时间窗口取最新采集日往前 7 天：信源结构需要足够样本才稳定，
--    只取一天会因 10 条样本剧烈跳动。前端脚注请标注"近 7 天"。
-- -----------------------------------------------------------------------------
create or replace view public.v_source_distribution as
with latest as (
  select project_id, max(metric_date) as as_of
  from public.daily_metrics
  where engine is null
  group by project_id
),
pairs as (
  select distinct
    u.project_id,
    src.domain,
    r.id        as response_id,
    r.called_at
  from public.responses r
  join public.runs u
    on u.id = r.run_id
  -- 用 left join：项目还没算指标时也要能看信源，此时窗口以今天为准
  left join latest l
    on l.project_id = u.project_id
  cross join lateral jsonb_array_elements_text(
    coalesce(r.cited_domains, '[]'::jsonb)
  ) as src(domain)
  where r.status = 'success'
    and r.called_at >= coalesce(l.as_of, (now() at time zone 'utc')::date) - interval '6 days'
    and r.called_at <  coalesce(l.as_of, (now() at time zone 'utc')::date) + interval '1 day'
),
per_domain as (
  select
    project_id,
    domain,
    count(*)        as citation_count,
    max(called_at)  as last_seen_at
  from pairs
  group by project_id, domain
)
select
  project_id,
  domain as name,
  citation_count,
  round(
    100.0 * citation_count / sum(citation_count) over (partition by project_id),
    1
  ) as value,
  last_seen_at
from per_domain;

alter view public.v_source_distribution set (security_invoker = true);

-- -----------------------------------------------------------------------------
-- 4. 清掉旧演示表上 Supabase 表编辑器自动生成的策略
--    grant 已经在 00 里收回了，写不进去（报 42501），但留着
--    "Allow public insert access" 这种名字对后来人是误导，直接删。
--    删完 pg_policies 里三张旧表只剩 read_all_anon。
-- -----------------------------------------------------------------------------
drop policy if exists "Allow public insert access" on public.metrics;
drop policy if exists "Allow public read access"   on public.metrics;
drop policy if exists "Allow public read access"   on public.source_distribution;
drop policy if exists "Allow public read access"   on public.radar_metrics;

commit;

-- =============================================================================
-- 执行后自检
--
-- 下面这些检查已经整理成可执行脚本：docs/sql/04-verify.sql
-- 整段跑一次，事务内自动 rollback，结果直接看输出，不用手工拼 6 段 SQL。
-- 尤其第 2 项：业务表是空的时候，用原表数据自插自测插不出任何行，
-- 自然也不会报重复键 —— 04 里造了临时项目才能真的测到约束。
-- =============================================================================
-- 1) 约束到位
--    select conname, pg_get_constraintdef(oid)
--    from pg_constraint
--    where conrelid = 'public.daily_metrics'::regclass and contype = 'u';
--    期望：daily_metrics_unique ... UNIQUE NULLS NOT DISTINCT (project_id, metric_date, engine, subject)
--
-- 2) 重复聚合行插不进去
--    begin;
--    insert into daily_metrics (project_id, metric_date, engine, subject, is_own, sample_size)
--    select project_id, metric_date, engine, subject, is_own, sample_size
--    from daily_metrics where engine is null limit 1;
--    期望：duplicate key value violates unique constraint "daily_metrics_unique"
--    rollback;
--
-- 3) 样本量不累加
--    select * from v_sample_summary;   -- samples 应等于最新一天的 sample_size
--
-- 4) 旧策略已清
--    select tablename, policyname from pg_policies
--    where schemaname = 'public' order by tablename, policyname;
--    期望：三张旧表各只剩 read_all_anon（metrics 另有一条 SELECT 由 00 保留）
-- =============================================================================
