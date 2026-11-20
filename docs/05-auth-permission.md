# 认证与权限 v0.1

> 方案：Supabase Auth + RLS（对应 `docs/04-supabase-contract.md` 的 BaaS 直连）  
> 交付物：`docs/sql/02-auth.sql`（86 条语句，可整段执行）+ 这份说明  
> 状态：待前端确认接口形状，SQL 已可执行

---

## 0. 为什么没有 `/auth/        register` 这种接口

Supabase 方案下，注册、登录、签发 token、刷新 token、找回密码，**全部由 Supabase Auth 处理，请求根本不经过我们的服务器**。前端直接调 SDK：

```ts
await supabase.auth.signUp({ email, password })
```

所以在这个方案里，"写一个注册接口"这件事没有对应物。我能给你的、也是真正决定权限能不能立住的东西，是三样：

| 交付        | 作用                                              | 位置          |
| --------- | ----------------------------------------------- | ----------- |
| 两张表       | `profiles`（用户资料）、`project_members`（谁在哪个项目、什么角色） | SQL 第 1、2 节 |
| 三个触发器     | 注册即建资料、建项目即成为 owner、`updated_at` 自动更新           | SQL 第 3 节   |
| 一套 RLS 策略 | 决定"谁能看到哪些行、谁能改哪些行"                              | SQL 第 5、6 节 |

外加四个判定函数（第 4 节）和一个加成员的 RPC。

**执行顺序**（在 Supabase SQL Editor 里各跑一次，都幂等）：

```
docs/sql/01-schema.sql   -- 六张表 + 四个视图
docs/sql/02-auth.sql     -- 认证、角色、全部策略
```

`02` 第 7 节会 `alter view`，所以**必须先跑 `01`**，否则报视图不存在。

**这三样东西的严格程度，等于后端接口里所有 `if not has_permission` 判断的总和。** 区别只是：它写在数据库里，绕不过去；而写在代码里，总有某个接口忘了加。

---

## 1. 权限模型

### 1.1 角色

四个角色，够用且不过度设计：

| 角色       | 定位                    |
| -------- | --------------------- |
| `owner`  | 项目创建者。唯一，可以转让         |
| `admin`  | 可以改项目信息、管成员           |
| `member` | 日常使用者：改问题库、触发采集、看数据   |
| `viewer` | 只看数据，不能改任何东西（给客户看报表用） |

### 1.2 权限矩阵

| 操作                                           | owner | admin | member | viewer | 谁都不能                |
| -------------------------------------------- | ----- | ----- | ------ | ------ | ------------------- |
| 看看板、信源、雷达                                    | ✅     | ✅     | ✅      | ✅      |                     |
| 看回答原文、提及明细                                   | ✅     | ✅     | ✅      | ✅      |                     |
| 改问题库（增删改问题）                                  | ✅     | ✅     | ✅      | ❌      |                     |
| 触发采集                                         | ✅     | ✅     | ✅      | ❌      |                     |
| 改项目信息（品牌、竞品、别名）                              | ✅     | ✅     | ❌      | ❌      |                     |
| 加/删成员、改角色                                    | ✅     | ✅     | ❌      | ❌      |                     |
| 写 `responses` / `mentions` / `daily_metrics` |       |       |        |        | **只有 service_role** |

最后一行是这套设计的核心：**采集结果任何人都不能通过前端写**。人只能在问题库里定义"问什么"，答案只能由采集器写进去。这样看板上的数字才有意义。

---

## 2. 表结构

### 2.1 `profiles`

```sql
create table if not exists public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  email        text,
  display_name text,
  avatar_url   text,
  last_seen_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
```

`auth.users` 里存的是邮箱、密码哈希、邮箱验证状态——**那些别碰，也别往里加业务字段**。`profiles` 只存我们自己的东西，主键和 `auth.users.id` 一一对应。

`last_seen_at` 需要前端在登录后顺手更新一次：

```ts
await supabase.from('profiles').update({ last_seen_at: new Date().toISOString() }).eq('id', user.id)
```

### 2.2 `project_members`

```sql
create table if not exists public.project_members (
  id         uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id    uuid not null references public.profiles(id) on delete cascade,
  role       text not null default 'viewer'
             check (role in ('owner','admin','member','viewer')),
  created_at timestamptz not null default now(),
  unique (project_id, user_id)
);
```

`user_id` 指向 `profiles` 而不是 `auth.users`，这点很关键：**`auth.users` 不暴露给 PostgREST**，指向它就没法写 `select('role, profiles(display_name)')` 这种联表查询，成员列表页就得多请求一轮。`profiles.id` 本身有到 `auth.users` 的级联外键，删账号照样连带清干净。

### 2.3 `projects` 补一列

```sql
alter table public.projects add column if not exists created_by uuid references auth.users(id);
alter table public.projects alter column created_by set default auth.uid();
```

加了默认值，前端 `insert` 时不用自己传 `created_by`，数据库自动填当前登录用户。同时 `projects_insert` 策略会校验 `created_by = auth.uid()`，想伪造创建者会被拒。

---

## 3. 三个触发器

| 触发器                    | 时机          | 做什么                |
| ---------------------- | ----------- | ------------------ |
| `on_auth_user_created` | 注册成功后       | 往 `profiles` 插一行   |
| `on_project_created`   | 建项目后        | 把创建者写成该项目的 `owner` |
| `trg_profiles_touch`   | 改 profile 前 | 更新 `updated_at`    |

第一个的完整实现：

```sql
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
  );
  return new;
end;
$$;
```

`display_name` 从注册时传的 `options.data.display_name` 取，取不到就用邮箱前缀。**前端注册时必须带上这个字段**，否则用户名全是邮箱前缀：

```ts
await supabase.auth.signUp({
  email, password,
  options: { data: { display_name: name } },   // 对应 raw_user_meta_data->>'display_name'
})
```

漏了这一步不会报错，只是名字不对——所以写在这里提醒。

---

## 4. 为什么判定函数必须 `security definer`

这是全套设计里唯一一个"不知道就会踩"的坑，单独说。

最直觉的写法是在策略里直接查表：

```sql
-- 错误写法
create policy members_read on public.project_members
  for select to authenticated
  using (exists (select 1 from public.project_members where ...));
```

`project_members` 自己开了 RLS，这条策略查 `project_members` 时又会触发一次策略 → 再查一次 → **无限递归，Postgres 直接报错**。

正确做法是把判断抽成函数，函数用 `security definer`：

```sql
create or replace function public.is_project_member(p_project uuid)
returns boolean
language sql stable security definer
set search_path = public, pg_temp     -- 必须固定 search_path，否则有被劫持风险
as $$
  select exists (
    select 1 from public.project_members
    where project_id = p_project and user_id = auth.uid()
  );
$$;
```

`security definer` 让函数以创建者（postgres）身份执行，绕过 `project_members` 自己的 RLS，只把"是/否"返回给策略用。

四个函数：

| 函数                              | 返回                      | 被谁用          |
| ------------------------------- | ----------------------- | ------------ |
| `is_project_member(project_id)` | 是不是成员（任何角色）             | 所有"读"策略      |
| `can_write(project_id)`         | 角色 ∈ owner/admin/member | 问题库写策略       |
| `can_admin(project_id)`         | 角色 ∈ owner/admin        | 项目改删、成员管理    |
| `shares_project(user_id)`       | 和某人是否同项目                | 读别人的 profile |

还有一个 RPC 函数 `add_member_by_email`，见第 6 节。

---

## 5. 策略清单

完整 SQL 在 `docs/sql/02-auth.sql` 第 6 节，这里给逻辑映射。每条策略都是 `to authenticated`，匿名用户完全进不来。

| 表                 | SELECT     | INSERT                    | UPDATE      | DELETE            |
| ----------------- | ---------- | ------------------------- | ----------- | ----------------- |
| `profiles`        | 自己 + 同项目成员 | 由触发器写，前端不插                | 只能改自己       | 不给                |
| `projects`        | 成员         | `created_by = auth.uid()` | `can_admin` | `can_admin`       |
| `project_members` | 成员         | `can_admin`               | `can_admin` | `can_admin` 且不是自己 |
| `prompts`         | 成员         | `can_write`               | `can_write` | `can_write`       |
| `runs`            | 成员         | **无策略**                   | 无           | 无                 |
| `responses`       | 成员         | **无策略**                   | 无           | 无                 |
| `mentions`        | 成员         | **无策略**                   | 无           | 无                 |
| `daily_metrics`   | 成员         | **无策略**                   | 无           | 无                 |

"无策略"的意思不是报错，是**该角色什么都做不了**——`authenticated` 读得到，写不进去。写只有 `service_role` 能做，而 `service_role` 绕过所有 RLS，所以采集器不需要任何策略。

`project_members` 的 DELETE 排除了自己（`user_id <> auth.uid()`），避免 owner 误删自己把项目变成孤儿。代价是**没有"退出项目"功能**，需要的话得单独写个 RPC，现在不做。

---

## 6. 成员管理：为什么需要一个 RPC

前端拿不到 `auth.users`，也就没法按邮箱查人。要么让管理员从 Supabase 后台复制 UUID（体验极差），要么封一个函数：

```sql
create or replace function public.add_member_by_email(
  p_project uuid, p_email text, p_role text default 'member'
) returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare v_uid uuid;
begin
  if not public.can_admin(p_project) then
    raise exception '没有权限：只有 owner 或 admin 可以添加成员';
  end if;
  select id into v_uid from public.profiles where lower(email) = lower(p_email) limit 1;
  if v_uid is null then
    raise exception '用户 % 不存在，请先创建该账号', p_email;
  end if;
  insert into public.project_members (project_id, user_id, role)
  values (p_project, v_uid, p_role)
  on conflict (project_id, user_id) do update set role = excluded.role;
end;
$$;
```

`security definer` 在这里的用途不是绕 RLS，而是**绕开"前端没权限查全部 profiles"**。所以函数内部第一件事必须是自己校验 `can_admin`，否则任何登录用户都能往任意项目加自己。

前端调用：

```ts
const { error } = await supabase.rpc('add_member_by_email', {
  p_project: projectId,
  p_email: 'someone@example.com',
  p_role: 'member',
})
if (error) alert(error.message)   // 中文错误信息直接可展示
```

---

## 7. 前端要写的代码

### 7.1 `lib/auth.ts`

```ts
import { supabase } from './supabase'

export async function signUp(email: string, password: string, name: string) {
  const { data, error } = await supabase.auth.signUp({
    email, password,
    options: { data: { display_name: name } },
  })
  if (error) throw error
  return data
}

export async function signIn(email: string, password: string) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password })
  if (error) throw error
  return data
}

export async function signOut() {
  await supabase.auth.signOut()
}

export async function getProfile() {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return null
  const { data } = await supabase
    .from('profiles')
    .select('id,email,display_name,avatar_url')
    .eq('id', user.id)
    .single()
  return data
}
```

### 7.2 登录页

```tsx
'use client'
const { error } = await supabase.auth.signInWithPassword({ email, password })
if (error) setErr(error.message)
else router.replace('/')
```

`error.message` 是英文（`Invalid login credentials`），要中文就在前端做个映射表，别指望 Supabase 返回中文。

### 7.3 路由守卫

token 存在 localStorage，**Next.js 的 middleware 读不到**（middleware 跑在服务端，只能看 cookie）。所以 M1 先用客户端守卫，够用：

```tsx
'use client'
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  useEffect(() => {
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session) router.replace('/login')
    })
    return () => sub.subscription.unsubscribe()
  }, [router])
  return <>{children}</>
}
```

用 `onAuthStateChange` 而不是 `getSession()`，因为前者会监听后续的 token 过期，用户开着页面放一天也不会突然白屏。

**代价**：首屏会闪一下（先渲染内容再跳转）。要彻底消除得换 `@supabase/ssr` + cookie 存储 + middleware，那是 M2 的事，现在不值得。

### 7.4 改密码、找回密码

```ts
// 已登录时改密码
await supabase.auth.updateUser({ password: newPassword })

// 忘记密码：发邮件
await supabase.auth.resetPasswordForEmail(email, {
  redirectTo: `${location.origin}/reset-password`,
})
```

`redirectTo` 必须在 Supabase 控制台的 **Redirect URLs** 白名单里，否则链接会跳回首页。

---

## 8. Supabase 控制台必改的四个设置

| 位置                                           | 改什么                                                                     | 为什么                                           |
| -------------------------------------------- | ----------------------------------------------------------------------- | --------------------------------------------- |
| Authentication → Sign In / Providers → Email | 关掉 **Confirm email**                                                    | 内部工具，注册后还要去邮箱点确认太麻烦。关掉后 `signUp` 直接返回 session |
| Authentication → Sign In / Providers         | 决定是否关掉 **Allow new users to sign up**                                   | 关了就只能由管理员在后台建账号，内部工具推荐关                       |
| Authentication → URL Configuration           | **Site URL** 填正式域名，**Redirect URLs** 加 `http://localhost:3000/**` 和正式域名 | 不改的话找回密码链接跳错站                                 |
| Project Settings → API                       | 如果生成过 `service_role`，**轮换一次**                                           | 那个 key 一旦贴到聊天里就等于作废了                          |

第一条尤其重要：**没关 Confirm email 的话，注册完 `signUp` 返回的 `session` 是 `null`**，前端会以为注册失败。

---

## 9. 从 `04` 迁移时的三处硬改动

`04` 第 5 节那套是"未登录、匿名可读"，现在全部推翻。执行 `02-auth.sql` 时会自动做掉，但要理解为什么：

1. **`revoke all ... from anon`** —— Supabase 新建表默认给 `anon` 授权，不显式收回，匿名用户照样读得到全部数据。


2. **视图必须 `security_invoker = true`** —— 这是最容易漏的一条。视图默认以"视图所有者"权限执行，**会绕过底层表的 RLS**。不加这一句，你给 `daily_metrics` 加了成员限制，但任何人查 `v_metric_cards` 还是能看到所有项目的数据。加了之后视图按查询者权限执行，底层 RLS 才生效。
3. **`grant select ... to authenticated`** —— 登录用户需要显式授权，`revoke` 之后不补就没有权限。

`anon` key 依然写在前端，这个不变——它现在的作用只是"证明我是这个 Supabase 项目的访客"，不带任何数据权限。真正决定能看到什么的是登录后拿到的 JWT。

---

## 10. 已知限制

1. **没有审计日志。** 谁在什么时候改了问题库、加了谁进项目，查不到。要的话加一张 `audit_log` 表，触发器写。
2. **登录失败次数没有限制。** Supabase 有基础限流，但没有"连续失败 5 次锁定账号"。公网可访问前要评估。
3. **最后一个 owner 无法主动退出项目。** 策略排除了删自己。转让 owner 的功能还没做。
4. **`display_name` 可以重名。** 成员列表里两个人同名时分不清，靠邮箱区分。
5. **邮件服务用的是 Supabase 自带的。** 免费额度每小时有限，批量邀请会被限流。要发中文邮件得配自定义 SMTP。

---

## 11. 待确认

| # | 问题 | 建议 |
|---|---|---|
| Q1 | 注册开放，还是只有管理员能建账号？ | 内部工具，关掉公开注册，管理员在 Supabase 后台建账号，再用 RPC 加进项目 |
| Q2 | 四个角色够不够？ | 先用四档。如果实际只有"能改/不能改"，砍到两档也来得及 |
| Q3 | 一个用户能看到多个项目吗？ | 表结构支持（多对多），前端要有项目切换器 |
| Q4 | 需要"退出项目"和"转让 owner"吗？ | M2 再做，现在缺这两个功能不影响演示 |
| Q5 | 要不要审计日志？ | 如果将来给外部客户用，要；内部自用可以先不要 |

---

## 附：给前端的话（可直接复制）

> 权限这块我没写成接口，Supabase 方案下没有 `/auth/login` 这种东西，注册登录直接调 SDK。我给你的是表、触发器和行级权限策略，跑一遍 `docs/sql/02-auth.sql` 就生效。
>
> 你要写四块：
>
> 1. **登录页**：`supabase.auth.signInWithPassword({ email, password })`
> 2. **注册**：`supabase.auth.signUp({ email, password, options: { data: { display_name: name } } })` —— `display_name` 这个字段名不能改，触发器按这个名字取值
> 3. **路由守卫**：用 `supabase.auth.onAuthStateChange`，没 session 就 `router.replace('/login')`。别用 middleware，token 在 localStorage 里 middleware 读不到
> 4. **成员管理**：列表用 `project_members.select('role, user_id, profiles(display_name, email)')`，加人用 `supabase.rpc('add_member_by_email', { p_project, p_email, p_role })`
>
> 三件事要注意：
>
> - 我要在 Supabase 后台关掉「Confirm email」，不然你 `signUp` 之后拿到的 `session` 是 `null`，会以为注册失败
> - `anon` key 继续用没问题，但现在所有数据都要登录后才看得到。没登录调 `v_metric_cards` 返回空数组，不是报错，别以为是接口挂了
> - 加人只能按邮箱，不能按用户名。对方账号得先存在
>
> 你现在页面里的数据请求不用改，只要在请求前确保已经登录，RLS 会自动把数据过滤成"只能看自己项目"。
