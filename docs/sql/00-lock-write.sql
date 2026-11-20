-- =============================================================================
-- 00 · 关闭匿名写权限（应急，5 分钟）
-- 版本 v0.1 / 2026-11-21
--
-- 执行方式：Supabase Dashboard → SQL Editor → 整段粘贴 → Run
-- 执行顺序：**最先执行**，在 01-schema.sql 之前
-- 幂等：可重复执行
--
-- 背景：
--   当前 public.metrics / source_distribution / radar_metrics 三张表
--   对 anon（也就是浏览器里那个 sb_publishable_ key）开放了写权限，
--   实测 POST 返回 201。加上仓库是 public、key 硬编码在 lib/supabase.ts，
--   等于任何拿到仓库地址的人都能改或清空看板数据。
--
-- 这段脚本做什么：
--   1. 三张表开启 RLS（行级安全）
--   2. 只留一条“人人可读”的策略，保证看板照常显示
--   3. 从 anon / authenticated / public 三个角色收回所有写权限
--
-- 不含什么：
--   不改表结构、不删数据、不动业务表。跑完看板读数据完全不受影响。
--   一旦 01-schema.sql + 02-auth.sql 建好正式的表和视图，
--   这三张演示表就可以直接删掉，本脚本随之作废。
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- 1. 开启 RLS
--    只开 RLS 不建策略 = 全拒（看板会变空白），所以下一步必须补读策略
-- -----------------------------------------------------------------------------
alter table public.metrics             enable row level security;
alter table public.source_distribution enable row level security;
alter table public.radar_metrics       enable row level security;

-- -----------------------------------------------------------------------------
-- 2. 只读策略：anon 和已登录用户都能读，但不能写
--    注意：策略只控制“行”，写不进去靠的是第 3 步收回 GRANT 权限
-- -----------------------------------------------------------------------------
drop policy if exists "read_all_anon" on public.metrics;
create policy "read_all_anon" on public.metrics
  for select to anon, authenticated using (true);

drop policy if exists "read_all_anon" on public.source_distribution;
create policy "read_all_anon" on public.source_distribution
  for select to anon, authenticated using (true);

drop policy if exists "read_all_anon" on public.radar_metrics;
create policy "read_all_anon" on public.radar_metrics
  for select to anon, authenticated using (true);

-- -----------------------------------------------------------------------------
-- 3. 收回写权限
--    Supabase 建表时默认给 anon / authenticated 授予了 ALL，
--    只开 RLS 不够干净，必须把 GRANT 一起收掉
--    连 public 伪角色一起收，防止通过默认权限再次继承
-- -----------------------------------------------------------------------------
revoke insert, update, delete, truncate, references, trigger
  on public.metrics, public.source_distribution, public.radar_metrics
  from anon, authenticated, public;

-- 读权限显式补回（revoke 上面没动 select，这里写一遍是为了幂等和可读）
grant select
  on public.metrics, public.source_distribution, public.radar_metrics
  to anon, authenticated;

commit;

-- =============================================================================
-- 验证（跑完单独执行这段，看结果对不对）
-- =============================================================================

-- ① 应该有 3 行，action 都是 SELECT，roles 是 {anon,authenticated}
select tablename, policyname, cmd, roles
  from pg_policies
 where schemaname = 'public'
   and tablename in ('metrics', 'source_distribution', 'radar_metrics')
 order by tablename;

-- ② 三个表 × 两个角色，只应出现 SELECT，不应出现 INSERT/UPDATE/DELETE
select table_name, grantee, privilege_type
  from information_schema.role_table_grants
 where table_schema = 'public'
   and table_name in ('metrics', 'source_distribution', 'radar_metrics')
   and grantee in ('anon', 'authenticated')
 order by table_name, grantee, privilege_type;

-- ③ RLS 应为 true
select relname, relrowsecurity
  from pg_class
 where relnamespace = 'public'::regnamespace
   and relname in ('metrics', 'source_distribution', 'radar_metrics');

-- =============================================================================
-- 回滚（万一看板读不出数据，确认是本脚本导致时执行这一段）
-- =============================================================================
-- begin;
-- alter table public.metrics             disable row level security;
-- alter table public.source_distribution disable row level security;
-- alter table public.radar_metrics       disable row level security;
-- grant all on public.metrics, public.source_distribution, public.radar_metrics
--   to anon, authenticated;
-- commit;

-- =============================================================================
-- 跑完之后会发生什么
-- =============================================================================
-- 1. 看板正常显示，三张表的数据照常读得到
-- 2. 前端“+ 新增指标”按钮会报错，弹窗提示“写入失败：new row violates
--    row-level security policy”。这是预期结果，不是故障
-- 3. 采集器将来用 service_role key 写入，service_role 不受 RLS 约束，无需额外授权
-- 4. 这三张表只是演示数据（4 行手工填的假值），等 01 / 02 建好正式结构后直接删掉
