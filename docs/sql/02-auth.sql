-- =============================================================================
-- GEO 平台 · 认证与权限（Supabase Auth + RLS）
-- 版本 v0.1 / 2026-11-20
--
-- 执行方式：Supabase Dashboard → SQL Editor → 整段粘贴 → Run
-- 前置：必须先执行 docs/04-supabase-contract.md 的第 3.1 节（建表）
--       和第 4 节（建视图），否则本文件第 7 节 alter view 会报视图不存在
-- 幂等：全部语句可重复执行
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. profiles：业务资料表
--    auth.users 归 Supabase 管（邮箱、密码哈希、验证状态），不要往里加业务字段
--    profiles 只存我们自己的东西
-- -----------------------------------------------------------------------------
create table if not exists public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  email        text,
  display_name text,
  avatar_url   text,
  last_seen_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- -----------------------------------------------------------------------------
-- 2. project_members：项目成员与角色
--    一个用户可以属于多个项目，每个项目一个角色
--
--    user_id 指向 profiles 而不是 auth.users：
--    auth.users 不暴露给 PostgREST，指过去就没法用 profiles(display_name) 联表查询。
--    profiles.id 本身有到 auth.users 的级联外键，删账号仍然会连带清干净。
-- -----------------------------------------------------------------------------
create table if not exists public.project_members (
  id         uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id    uuid not null references public.profiles(id) on delete cascade,
  role       text not null default 'viewer'
             check (role in ('owner','admin','member','viewer')),
  created_at timestamptz not null default now(),
  unique (project_id, user_id)
);

create index if not exists idx_members_user    on public.project_members(user_id);
create index if not exists idx_members_project on public.project_members(project_id);

-- projects 补一列：谁建的。默认取当前登录用户，前端不用传
alter table public.projects
  add column if not exists created_by uuid references auth.users(id);

alter table public.projects
  alter column created_by set default auth.uid();


-- -----------------------------------------------------------------------------
-- 3. 触发器
-- -----------------------------------------------------------------------------

-- 3.1 注册即建 profile
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(
      nullif(new.raw_user_meta_data->>'display_name', ''),
      split_part(coalesce(new.email, 'user'), '@', 1)
    )
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 3.2 建项目即成为 owner
create or replace function public.handle_new_project()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.created_by is not null then
    insert into public.project_members (project_id, user_id, role)
    values (new.id, new.created_by, 'owner')
    on conflict (project_id, user_id) do nothing;
  end if;
  return new;
end;
$$;

drop trigger if exists on_project_created on public.projects;
create trigger on_project_created
  after insert on public.projects
  for each row execute function public.handle_new_project();

-- 3.3 updated_at 自动维护
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_profiles_touch on public.profiles;
create trigger trg_profiles_touch
  before update on public.profiles
  for each row execute function public.touch_updated_at();


-- -----------------------------------------------------------------------------
-- 4. 权限判定函数
--
--    为什么必须 security definer：
--    RLS 策略里如果直接写 exists (select 1 from project_members ...)，
--    project_members 自己也有 RLS，会再触发一次策略检查 → 无限递归报错。
--    security definer 让函数以创建者（postgres）身份执行，绕过该表的 RLS，
--    只把"是/否"结果返回给策略用。
-- -----------------------------------------------------------------------------

-- 4.1 是不是这个项目的成员
create or replace function public.is_project_member(p_project uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.project_members
    where project_id = p_project and user_id = auth.uid()
  );
$$;

-- 4.2 能不能改数据（owner / admin / member）
create or replace function public.can_write(p_project uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.project_members
    where project_id = p_project
      and user_id = auth.uid()
      and role in ('owner','admin','member')
  );
$$;

-- 4.3 能不能管项目（owner / admin）：改项目信息、管成员
create or replace function public.can_admin(p_project uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.project_members
    where project_id = p_project
      and user_id = auth.uid()
      and role in ('owner','admin')
  );
$$;

-- 4.4 是不是同一个项目的成员（用于读别人的 profile）
create or replace function public.shares_project(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.project_members a
    join public.project_members b on b.project_id = a.project_id
    where a.user_id = auth.uid() and b.user_id = p_user
  );
$$;

-- 4.5 按邮箱加成员
--     前端查不到 auth.users，只能查 profiles；封装成 RPC，前端传邮箱即可
--     内部先校验调用者是不是该项目的 owner/admin，再插入
create or replace function public.add_member_by_email(
  p_project uuid,
  p_email   text,
  p_role    text default 'member'
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_uid uuid;
begin
  if not public.can_admin(p_project) then
    raise exception '没有权限：只有 owner 或 admin 可以添加成员';
  end if;

  if p_role not in ('admin','member','viewer') then
    raise exception '角色只能是 admin / member / viewer';
  end if;

  select id into v_uid
  from public.profiles
  where lower(email) = lower(p_email)
  limit 1;

  if v_uid is null then
    raise exception '用户 % 不存在，请先创建该账号', p_email;
  end if;

  -- 不能通过这个接口降级 owner，否则项目会失去唯一所有者
  if exists (
    select 1 from public.project_members
    where project_id = p_project and user_id = v_uid and role = 'owner'
  ) then
    raise exception '该用户是项目所有者，不能通过此接口调整角色';
  end if;

  insert into public.project_members (project_id, user_id, role)
  values (p_project, v_uid, p_role)
  on conflict (project_id, user_id)
  do update set role = excluded.role;
end;
$$;

revoke all on function public.is_project_member(uuid) from public;
revoke all on function public.can_write(uuid)         from public;
revoke all on function public.can_admin(uuid)         from public;
revoke all on function public.shares_project(uuid)    from public;
revoke all on function public.add_member_by_email(uuid, text, text) from public;

grant execute on function public.is_project_member(uuid) to authenticated;
grant execute on function public.can_write(uuid)         to authenticated;
grant execute on function public.can_admin(uuid)         to authenticated;
grant execute on function public.shares_project(uuid)    to authenticated;
grant execute on function public.add_member_by_email(uuid, text, text) to authenticated;


-- -----------------------------------------------------------------------------
-- 5. 开 RLS + 授权给 authenticated
--    注意：04 那版是给 anon 授权，这里全部收回，改成登录用户才有权限
-- -----------------------------------------------------------------------------

alter table public.profiles        enable row level security;
alter table public.project_members enable row level security;
alter table public.projects        enable row level security;
alter table public.prompts         enable row level security;
alter table public.runs            enable row level security;
alter table public.responses       enable row level security;
alter table public.mentions        enable row level security;
alter table public.daily_metrics   enable row level security;

-- Supabase 新建的表默认会给 anon / authenticated 授权，必须显式收回
revoke all on public.projects, public.prompts, public.runs,
              public.responses, public.mentions, public.daily_metrics,
              public.profiles, public.project_members from anon;
revoke all on public.projects, public.prompts, public.runs,
              public.responses, public.mentions, public.daily_metrics,
              public.profiles, public.project_members from public;

grant select, insert, update, delete on public.projects        to authenticated;
grant select, insert, update, delete on public.prompts         to authenticated;
grant select                          on public.runs           to authenticated;
grant select                          on public.responses      to authenticated;
grant select                          on public.mentions       to authenticated;
grant select                          on public.daily_metrics  to authenticated;
grant select, update                  on public.profiles       to authenticated;
grant select, insert, update, delete  on public.project_members to authenticated;


-- -----------------------------------------------------------------------------
-- 6. 策略
-- -----------------------------------------------------------------------------

-- 6.1 profiles
drop policy if exists profiles_read on public.profiles;
create policy profiles_read on public.profiles
  for select to authenticated
  using (id = auth.uid() or public.shares_project(id));

drop policy if exists profiles_update_self on public.profiles;
create policy profiles_update_self on public.profiles
  for update to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

-- 6.2 projects
drop policy if exists projects_read on public.projects;
create policy projects_read on public.projects
  for select to authenticated
  using (public.is_project_member(id));

drop policy if exists projects_insert on public.projects;
create policy projects_insert on public.projects
  for insert to authenticated
  with check (created_by = auth.uid());

drop policy if exists projects_update on public.projects;
create policy projects_update on public.projects
  for update to authenticated
  using (public.can_admin(id))
  with check (public.can_admin(id));

drop policy if exists projects_delete on public.projects;
create policy projects_delete on public.projects
  for delete to authenticated
  using (public.can_admin(id));

-- 6.3 project_members
--     能看自己项目的成员；只有 owner/admin 能增删改
drop policy if exists members_read on public.project_members;
create policy members_read on public.project_members
  for select to authenticated
  using (public.is_project_member(project_id) or user_id = auth.uid());

drop policy if exists members_insert on public.project_members;
create policy members_insert on public.project_members
  for insert to authenticated
  with check (public.can_admin(project_id));

drop policy if exists members_update on public.project_members;
create policy members_update on public.project_members
  for update to authenticated
  using (public.can_admin(project_id))
  with check (public.can_admin(project_id));

drop policy if exists members_delete on public.project_members;
create policy members_delete on public.project_members
  for delete to authenticated
  using (public.can_admin(project_id) and user_id <> auth.uid());

-- 6.4 prompts
drop policy if exists prompts_read on public.prompts;
create policy prompts_read on public.prompts
  for select to authenticated
  using (public.is_project_member(project_id));

drop policy if exists prompts_write on public.prompts;
create policy prompts_write on public.prompts
  for all to authenticated
  using (public.can_write(project_id))
  with check (public.can_write(project_id));

-- 6.5 runs / responses / mentions / daily_metrics
--     只读给成员。写权限不给任何 authenticated 策略 —— 只有 service_role（采集器）能写
drop policy if exists runs_read on public.runs;
create policy runs_read on public.runs
  for select to authenticated
  using (public.is_project_member(project_id));

drop policy if exists responses_read on public.responses;
create policy responses_read on public.responses
  for select to authenticated
  using (exists (
    select 1 from public.runs r
    where r.id = responses.run_id and public.is_project_member(r.project_id)
  ));

drop policy if exists mentions_read on public.mentions;
create policy mentions_read on public.mentions
  for select to authenticated
  using (exists (
    select 1
    from public.responses resp
    join public.runs r on r.id = resp.run_id
    where resp.id = mentions.response_id and public.is_project_member(r.project_id)
  ));

drop policy if exists daily_metrics_read on public.daily_metrics;
create policy daily_metrics_read on public.daily_metrics
  for select to authenticated
  using (public.is_project_member(project_id));


-- -----------------------------------------------------------------------------
-- 7. 视图：必须改成 security_invoker
--
--    默认视图以「视图所有者」权限执行，会绕过底层表的 RLS，
--    等于任何人查视图都能看到全部项目的数据。
--    security_invoker = true 后，视图按「查询者」权限执行，
--    底层 daily_metrics / responses 的 RLS 才会生效。
-- -----------------------------------------------------------------------------

alter view public.v_metric_cards        set (security_invoker = true);
alter view public.v_source_distribution set (security_invoker = true);
alter view public.v_radar_metrics       set (security_invoker = true);
alter view public.v_sample_summary      set (security_invoker = true);

revoke all   on public.v_metric_cards, public.v_source_distribution,
                public.v_radar_metrics, public.v_sample_summary from anon;
grant select on public.v_metric_cards, public.v_source_distribution,
                public.v_radar_metrics, public.v_sample_summary to authenticated;


-- -----------------------------------------------------------------------------
-- 8. 收尾自检（可选，跑一遍看结果）
-- -----------------------------------------------------------------------------
-- select tablename, rowsecurity from pg_tables
--   where schemaname = 'public' order by tablename;
-- select schemaname, viewname, qual from pg_views
--   where schemaname = 'public' and viewname like 'v_%';
