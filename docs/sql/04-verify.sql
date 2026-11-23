-- =============================================================================
-- 04-verify.sql —— 03-fixes.sql 执行后的验收脚本（只读，跑完自动回滚）
--
-- 用法：在 Supabase SQL Editor 里整段执行，把输出贴回来。
-- 设计要点：
--   * 整个过程在一个事务里，最后 rollback，不留任何测试数据；
--   * 需要写数据的检查都插到同一个假项目下，避免"表是空的所以测不出来"；
--   * 唯一约束的检查放在子事务里捕获 unique_violation，
--     不会因为预期中的报错把整个脚本中断。
--
-- 前置：00 / 01 / 02 / 03 都执行过。
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- 0. 连的是谁、什么版本（确认没连错库）
-- -----------------------------------------------------------------------------
select current_user as db_user, version() as pg_version;

-- -----------------------------------------------------------------------------
-- 1. 唯一约束定义
--    期望：daily_metrics_unique | UNIQUE NULLS NOT DISTINCT (project_id, metric_date, engine, subject)
-- -----------------------------------------------------------------------------
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = 'public.daily_metrics'::regclass
  and contype = 'u';

-- -----------------------------------------------------------------------------
-- 2. 四个视图 + security_invoker
--    期望：4 行，reloptions 都含 security_invoker=true
-- -----------------------------------------------------------------------------
select c.relname as view_name,
       coalesce(array_to_string(c.reloptions, ','), '(无)') as reloptions
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'v'
order by 1;

-- -----------------------------------------------------------------------------
-- 3. 策略清单
--    期望：三张旧表（metrics / source_distribution / radar_metrics）只剩 read_all_anon，
--          不再出现任何 Allow public ...
-- -----------------------------------------------------------------------------
select tablename, policyname, cmd, roles::text as roles
from pg_policies
where schemaname = 'public'
order by tablename, policyname;

-- -----------------------------------------------------------------------------
-- 4. 造一个临时项目（同一事务，最后回滚）
--    created_by 显式给 null：默认值是 auth.uid()，在 SQL Editor 里没有 JWT 上下文
-- -----------------------------------------------------------------------------
insert into public.projects (id, name, brand, created_by)
values ('00000000-0000-0000-0000-0000000000ff', '__verify__', '__verify__', null)
on conflict (id) do nothing;

-- -----------------------------------------------------------------------------
-- 5. 重复的"全引擎汇总"行必须被拒
--    engine is null 时旧约束形同虚设，这条是 03 的核心修复。
--    结果写进临时表再查出来——SQL Editor 里 raise notice 不一定会显示。
-- -----------------------------------------------------------------------------
create temp table _verify_result (step text, result text) on commit drop;

do $$
declare
  pid uuid := '00000000-0000-0000-0000-0000000000ff';
begin
  insert into public.daily_metrics
    (project_id, metric_date, engine, subject, is_own, sample_size, mention_rate)
  values (pid, date '2026-01-01', null, '__verify__', true, 10, 0.5);

  begin
    insert into public.daily_metrics
      (project_id, metric_date, engine, subject, is_own, sample_size, mention_rate)
    values (pid, date '2026-01-01', null, '__verify__', true, 10, 0.5);
    insert into _verify_result values ('5 唯一约束', 'FAIL 重复行被插进去了');
  exception
    when unique_violation then
      insert into _verify_result values ('5 唯一约束', 'OK ' || sqlerrm);
  end;
end
$$;

select step, result from _verify_result order by step;

-- -----------------------------------------------------------------------------
-- 6. 样本量只取最新一天
--    已有 01-01 = 10，再插 01-02 = 20
--    期望：as_of = 2026-01-02，samples = 20
--    旧版（累加）会给 30
-- -----------------------------------------------------------------------------
insert into public.daily_metrics
  (project_id, metric_date, engine, subject, is_own, sample_size, mention_rate)
values ('00000000-0000-0000-0000-0000000000ff', date '2026-01-02', null, '__verify__', true, 20, 0.6);

select project_id, as_of, samples
from public.v_sample_summary
where project_id = '00000000-0000-0000-0000-0000000000ff';

-- -----------------------------------------------------------------------------
-- 7. 信源分布：单条回答内去重 + 跨回答累加 + 7 天窗口
--    回答 A 引用 ["zhihu.com","zhihu.com","wikipedia.org"]（同一条里重复写两次知乎）
--    回答 B 引用 ["zhihu.com"]
--    期望：zhihu.com 2 / 66.7，wikipedia.org 1 / 33.3
--    旧版（按出现次数）会给 zhihu.com 3 / 75.0
-- -----------------------------------------------------------------------------
insert into public.prompts (id, project_id, text)
values ('00000000-0000-0000-0000-0000000000a1',
        '00000000-0000-0000-0000-0000000000ff', '__verify__');

insert into public.runs (id, project_id, status, prompt_count, engine_count)
values ('00000000-0000-0000-0000-0000000000b1',
        '00000000-0000-0000-0000-0000000000ff', 'success', 1, 1);

insert into public.responses
  (id, run_id, prompt_id, engine, question, answer_text, cited_domains, status, called_at)
values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000b1',
   '00000000-0000-0000-0000-0000000000a1', 'deepseek', '__verify__', 'A',
   '["zhihu.com","zhihu.com","wikipedia.org"]'::jsonb, 'success',
   timestamptz '2026-01-02 03:00:00+00'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000b1',
   '00000000-0000-0000-0000-0000000000a1', 'deepseek', '__verify__', 'B',
   '["zhihu.com"]'::jsonb, 'success',
   timestamptz '2026-01-02 04:00:00+00');

select name, citation_count, value
from public.v_source_distribution
where project_id = '00000000-0000-0000-0000-0000000000ff'
order by name;

-- -----------------------------------------------------------------------------
-- 8. 窗口外的旧回答不能进来
--    再插一条 2025-01-01 的回答，期望它不影响上面的统计（结果仍为 2 / 1）
-- -----------------------------------------------------------------------------
insert into public.responses
  (id, run_id, prompt_id, engine, question, answer_text, cited_domains, status, called_at)
values
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000b1',
   '00000000-0000-0000-0000-0000000000a1', 'deepseek', '__verify__', 'C',
   '["baidu.com"]'::jsonb, 'success', timestamptz '2025-01-01 03:00:00+00');

select name, citation_count, value
from public.v_source_distribution
where project_id = '00000000-0000-0000-0000-0000000000ff'
order by name;

-- -----------------------------------------------------------------------------
-- 9. 回滚：上面插的东西全部撤销
-- -----------------------------------------------------------------------------
rollback;

-- -----------------------------------------------------------------------------
-- 10. 回滚后确认业务表仍是 0 行
--     期望：每张表都是 0（如果你自己灌过数据，按实际值判断，只要没多出 __verify__ 就行）
-- -----------------------------------------------------------------------------
select 'projects'       as table_name, count(*) from public.projects
union all
select 'prompts',        count(*) from public.prompts
union all
select 'runs',           count(*) from public.runs
union all
select 'responses',      count(*) from public.responses
union all
select 'mentions',       count(*) from public.mentions
union all
select 'daily_metrics',  count(*) from public.daily_metrics
order by 1;
