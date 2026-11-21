# 前端对接清单（v1 · 2026-11-21）

这份是发给前端同学的。**照着做，大约 15 分钟**，做完之后你的页面数据就从「手填的假表格」换成真实业务表算出来的数字。

现在的情况：线上 Supabase 里只有三张图表表（`metrics` / `source_distribution` / `radar_metrics`），每张 4 行手填数据，而且**任何拿到仓库地址的人都能改**（RLS 没开、key 硬编码在公开仓库里）。业务表一张都没有，所以后端采集器现在无处写入。

## 一、六件事，按顺序做

| # | 做什么 | 在哪做 | 耗时 |
|---|---|---|---|
| 1 | 执行 `00-lock-write.sql` | Supabase → SQL Editor | 1 分钟 |
| 2 | 执行 `01-schema.sql` | 同上 | 2 分钟 |
| 3 | 执行 `02-auth.sql` | 同上 | 3 分钟 |
| 4 | 执行 `03-fixes.sql`（实测后的修正） | 同上 | 1 分钟 |
| 5 | 关掉 Confirm email、加 Redirect URL | Supabase → Authentication | 2 分钟 |
| 6 | 把数据库连接串给我 | Supabase → Project Settings → Database | 1 分钟 |

四个 `.sql` 文件我单独发你，都在后端仓库 `docs/sql/` 下。**顺序不能乱**，`03` 依赖前三个建好的表和视图。

## 二、每一步的具体操作

### 第 1 步：关掉匿名写权限

打开 Supabase 控制台 → 左侧 **SQL Editor** → **New query** → 把 `00-lock-write.sql` 全文粘进去 → 点 **Run**。

**预期**：显示 `Success. No rows returned`。

跑完单独把文件末尾的「验证」三段分别执行一次，应该看到：

- 权限列表里只有 `SELECT`，没有 `INSERT` / `UPDATE` / `DELETE`
- `relrowsecurity` 三行都是 `true`
- 策略行数**这一步先不管**：可能看到 7 行而不是 3 行。多出来的是 Supabase 表编辑器早先自动生成的 `Allow public ... access`，第 4 步的 `03-fixes.sql` 会把它们清掉

**会发生什么**：看板数据照常显示。但「+ 新增指标」按钮点保存会报错：

```
42501 permission denied for table metrics
```

**这是预期的，不是 bug。** 报错是"没权限"而不是"违反行级安全策略"，因为表权限在 RLS 之前检查——先把 grant 收回，连走到 RLS 那一步的机会都没有。那个弹窗本来就要删：数据库不是表格，指标应该由采集器算出来，不该手填一个 `"42.1%"` 字符串进去。删按钮这件事等表建好后一起做。

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

### 第 4 步：打三个修正补丁

同样方式，粘 `03-fixes.sql` → Run。这是线上实测后补的，修三个问题：

| 问题 | 不修的后果 | 修法 |
|---|---|---|
| `daily_metrics` 唯一约束挡不住 `engine is null` 的重复行 | 采集重跑后同一天存进两份汇总，看板数字随机漂移 | 换成 `unique nulls not distinct`（PG 15+） |
| 样本量把历史各天累加了 | 圆心显示 7 天总和，日期却标着最新一天 | 只取最新一天的 `sample_size` |
| 信源分布把单条回答内重复域名算了多次，且没有时间窗口 | 环形图占比虚高，跟 KPI 卡片口径对不上 | 按回答去重计数 + 最近 7 天窗口 |

**验证**（新建 query 执行）：

```sql
-- 1. 约束应该带 NULLS NOT DISTINCT
select conname, pg_get_constraintdef(oid) from pg_constraint
 where conrelid = 'public.daily_metrics'::regclass and contype = 'u';

-- 2. 三张旧表只剩 read_all_anon，历史策略已清
select tablename, policyname from pg_policies
 where schemaname = 'public' order by tablename, policyname;
```

脚本是幂等的，中途报错可以整段重跑。加约束前会自动删掉可能已存在的重复行（保留一条），所以不用手动清理。

> **前端注意**：`v_source_distribution` 现在统计**最近 7 天**，不是最新一天。环形图脚注写"近 7 天"，别写"今日"。`citation_count` 的含义也变成"引用了该域名的回答数"，不是域名出现次数——如果界面上有"引用次数"文案，建议改成"引用回答数"。

### 第 5 步：两个认证设置

Supabase → **Authentication** → **Sign In / Providers** → **Email**：

- **关掉 Confirm email**。不关的话 `signUp()` 返回的 `session` 是 `null`，前端会以为注册失败。
- 顺手确认 Email provider 是启用的。

Supabase → **Authentication** → **URL Configuration**：

- **Site URL** 填 `http://localhost:3000`
- **Redirect URLs** 加一条 `http://localhost:3000/**`

不配这个，找回密码的邮件链接会跳到 `https://xxx.supabase.co` 而不是你的页面。

### 第 6 步：给我连接串

Supabase → **Project Settings** → **Database** → **Connection string** → 选 **URI** 标签，复制那串 `postgresql://postgres:...`。

**发我这个，不用发 service_role key。** 原因：

- 我拿到连接串就能自己把四个脚本跑完、验证语义（列名、视图类型这些语法检查发现不了的问题），报错我自己修，不用你来回贴日志
- 连接串只在服务器环境变量里，不进仓库

如果你更愿意给 `service_role` key 也行，但**它绝对不能出现在前端代码或仓库里**。那个 key 绕过所有 RLS，泄漏等于数据库全裸。

> 顺便说一句：`lib/supabase.ts` 里那个 `sb_publishable_...` 是公开 key，设计上就是给浏览器用的，不算泄漏。但仓库是 public，建议后面把 URL 和 key 挪到 `.env.local`（`NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`），改起来 5 分钟。不急，等表跑通再说。

## 三、跑完回我三个信息

1. 四个脚本是否有报错（有就贴报错原文，别截图，我要复制文字）
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

## 附：后端接口（现在就能用）

`docs/08-backend-api.md` 里是后端新增的四个接口，已经写完并测过：

| 方法 | 路径 | 谁能调 |
|---|---|---|
| GET | `/api/v1/auth/me` | 登录用户 |
| GET | `/api/v1/projects/{id}/runs` | 项目成员（含 viewer） |
| POST | `/api/v1/projects/{id}/collect` | member 及以上 |
| POST | `/api/v1/projects/{id}/metrics/recompute` | member 及以上 |

调用方式和 `supabase-js` 的区别只有一处：请求头加 `Authorization: Bearer <session.access_token>`，也就是 `supabase.auth.getSession()` 里的那个 token。

优先级上，**先把上面四张表跑通、看板数据换成真表**，再接后端接口——后端接口依赖 `project_members` 里的角色数据，表没建好调它只会拿到 401/404。

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
