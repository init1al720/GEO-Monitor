# 前端对接清单（v1 · 2026-11-21）

这份是发给前端同学的。**照着做，大约 15 分钟**，做完之后你的页面数据就从「手填的假表格」换成真实业务表算出来的数字。

现在的情况：线上 Supabase 里只有三张图表表（`metrics` / `source_distribution` / `radar_metrics`），每张 4 行手填数据，而且**任何拿到仓库地址的人都能改**（RLS 没开、key 硬编码在公开仓库里）。业务表一张都没有，所以后端采集器现在无处写入。

## 一、五件事，按顺序做

| # | 做什么 | 在哪做 | 耗时 |
|---|---|---|---|
| 1 | 执行 `00-lock-write.sql` | Supabase → SQL Editor | 1 分钟 |
| 2 | 执行 `01-schema.sql` | 同上 | 2 分钟 |
| 3 | 执行 `02-auth.sql` | 同上 | 3 分钟 |
| 4 | 关掉 Confirm email、加 Redirect URL | Supabase → Authentication | 2 分钟 |
| 5 | 把数据库连接串给我 | Supabase → Project Settings → Database | 1 分钟 |

三个 `.sql` 文件我单独发你，都在后端仓库 `docs/sql/` 下。

## 二、每一步的具体操作

### 第 1 步：关掉匿名写权限

打开 Supabase 控制台 → 左侧 **SQL Editor** → **New query** → 把 `00-lock-write.sql` 全文粘进去 → 点 **Run**。

**预期**：显示 `Success. No rows returned`。

跑完单独把文件末尾的「验证」三段分别执行一次，应该看到：

- 3 行策略，`cmd` 都是 `SELECT`
- 权限列表里只有 `SELECT`，没有 `INSERT` / `UPDATE` / `DELETE`
- `relrowsecurity` 三行都是 `true`

**会发生什么**：看板数据照常显示。但「+ 新增指标」按钮点保存会报错：

```
写入失败：new row violates row-level security policy for table "metrics"
```

**这是预期的，不是 bug。** 那个弹窗本来就要删——数据库不是表格，指标应该由采集器算出来，不该手填一个 `"42.1%"` 字符串进去。删按钮这件事等表建好后一起做。

### 第 2 步：建业务表与视图

同样在 SQL Editor，新建一个 query，粘 `01-schema.sql` → Run。

**预期**：一步步滚出 `Success`，最后没有报错。

**验证**（新建 query 执行）：

```sql
select table_name from information_schema.tables
 where table_schema = 'public' and table_type = 'BASE TABLE'
 order by table_name;
```

应该看到 6 张新表：`daily_metrics` / `mentions` / `projects` / `prompts` / `responses` / `runs`（外加你原来的三张图表表）。

再验证视图：

```sql
select table_name from information_schema.views
 where table_schema = 'public' order by table_name;
```

应该有 4 个：`v_metric_cards` / `v_radar_metrics` / `v_sample_summary` / `v_source_distribution`。

> 视图现在是空的（没有数据），查出来是空数组，不报错。这是正常的。

### 第 3 步：认证与权限

同样方式，粘 `02-auth.sql` → Run。

**预期**：会新建 `profiles`、`project_members` 两张表，加若干触发器和策略。

**验证**：

```sql
select tablename, policyname from pg_policies
 where schemaname = 'public' order by tablename;
```

应该有几十行策略，覆盖 `projects` / `prompts` / `runs` / `responses` / `mentions` / `daily_metrics` / `profiles` / `project_members`。

**注意**：`responses` / `mentions` / `daily_metrics` 这三张表**故意没有写策略**。也就是说任何人（包括登录用户）都写不进去，只有后端的 `service_role` 能写。这是设计，不是漏配——否则演示时谁都能插一条数据，看板就不可信了。

### 第 4 步：两个认证设置

Supabase → **Authentication** → **Sign In / Providers** → **Email**：

- **关掉 Confirm email**。不关的话 `signUp()` 返回的 `session` 是 `null`，前端会以为注册失败。
- 顺手确认 Email provider 是启用的。

Supabase → **Authentication** → **URL Configuration**：

- **Site URL** 填 `http://localhost:3000`
- **Redirect URLs** 加一条 `http://localhost:3000/**`

不配这个，找回密码的邮件链接会跳到 `https://xxx.supabase.co` 而不是你的页面。

### 第 5 步：给我连接串

Supabase → **Project Settings** → **Database** → **Connection string** → 选 **URI** 标签，复制那串 `postgresql://postgres:...`。

**发我这个，不用发 service_role key。** 原因：

- 我拿到连接串就能自己把三个脚本跑完、验证语义（列名、视图类型这些语法检查发现不了的问题），报错我自己修，不用你来回贴日志
- 连接串只在服务器环境变量里，不进仓库

如果你更愿意给 `service_role` key 也行，但**它绝对不能出现在前端代码或仓库里**。那个 key 绕过所有 RLS，泄漏等于数据库全裸。

> 顺便说一句：`lib/supabase.ts` 里那个 `sb_publishable_...` 是公开 key，设计上就是给浏览器用的，不算泄漏。但仓库是 public，建议后面把 URL 和 key 挪到 `.env.local`（`NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`），改起来 5 分钟。不急，等表跑通再说。

## 三、跑完回我三个信息

1. 三个脚本是否有报错（有就贴报错原文，别截图，我要复制文字）
2. 数据库连接串
3. 第 1 步验证查询的结果（截图或文字都行，我确认写权限真的关掉了）

## 四、之后要改前端的地方（先不用动，等表建好我给清单）

| 现在 | 之后 |
|---|---|
| `page.tsx` 读 `metrics` 表 | 读 `v_metric_cards` 视图 |
| `SourceChart.tsx` 读 `source_distribution` | 读 `v_source_distribution` |
| `RadarChart.tsx` 读 `radar_metrics` | 读 `v_radar_metrics`（`a`/`b` 变成 `own_value`/`competitor_value`） |
| 环形图圆心写死 `1,284` | 读 `v_sample_summary.samples` |
| 卡片按数组下标上色 | 用视图返回的 `direction` / `is_good` |
| 「+ 新增指标」弹窗 | 删除 |
| 雷达图标题写死「智标枪 vs 广联达」 | 从数据读 |
| 没有登录 | 加登录页 + `onAuthStateChange` 守卫 |

最后一条有个坑先说：**路由守卫不要用 Next.js middleware**，因为 token 存在 localStorage 里，middleware 跑在服务端读不到，会一直判定未登录。用客户端组件里的 `onAuthStateChange`。

## 五、常见错误对照

| 报错 | 原因 | 怎么解 |
|---|---|---|
| `PGRST205 Could not find the table 'public.projects'` | 表没建，或 PostgREST 缓存没刷新 | 先跑 `01-schema.sql`；跑完等 10 秒，或 Settings → API → Reload schema |
| `42P01 relation "public.projects" does not exist` | 同上，SQL 层面找不到表 | 同上 |
| `infinite recursion detected in policy for relation "project_members"` | 策略里直接查了开了 RLS 的表 | `02-auth.sql` 已用 `security definer` 函数规避，别自己改回直查写法 |
| `new row violates row-level security policy` | 正常现象（第 1 步之后写库被拦） | 不需要修，正好证明权限关了 |
| 看板变空白、控制台一堆 401 | 第 1 步的读策略没建成功 | 执行 `00-lock-write.sql` 末尾的回滚块，再重跑一遍脚本 |
| `permission denied for table metrics` | 读权限被误收 | `grant select on public.metrics to anon;` |

## 六、三件不要做的事

1. **不要把 `service_role` key 写进任何前端文件**，包括 `.env`（Next.js 的 `NEXT_PUBLIC_*` 会被打包进浏览器代码）
2. **不要手动往 `responses` / `mentions` / `daily_metrics` 插数据**，即使能插。这三个表的数字代表真实采集结果，手插一条，整份报表就不可信了
3. **不要改 `01-schema.sql` 里的字段名**。后端模型、视图、聚合逻辑全都按这套名字写死了，改一个字段要连带改四处

有问题直接把报错贴给我，别自己猜着改表结构 —— 改坏了要重建，前面的数据就白采了。
