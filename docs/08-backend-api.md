# 后端接口 v0.1（鉴权 + 采集触发）

> 对应代码：`app/core/security.py`、`app/api/deps.py`、`app/api/v1/endpoints/auth.py`、`projects.py`
> 测试：`tests/test_auth.py`（19 个用例）

---

## 1. 这套接口不做什么

**没有注册、登录、改密码、刷新 token 的接口。** 这些请求由前端直连 Supabase Auth 完成，根本不经过我们的服务器：

```ts
const { data, error } = await supabase.auth.signInWithPassword({ email, password })
const token = data.session.access_token
```

我们的后端只做三件事：

1. **验证** Supabase 签发的 token（签名、有效期、iss、aud）；
2. **查出**这个人在这个项目里是什么角色（查 `project_members`）；
3. 角色够，才允许**触发采集 / 重算指标**。

为什么触发采集要放后端、不能让浏览器直接调：采集会调用第三方模型接口、产生真实费用，而且需要引擎密钥。密钥只存在服务器上，浏览器永远拿不到。

---

## 2. 鉴权流程

```
浏览器                          Supabase Auth            我们后端
  │  signInWithPassword          │                        │
  │─────────────────────────────>│                        │
  │  access_token (JWT)          │                        │
  │<─────────────────────────────│                        │
  │                                                       │
  │  GET /api/v1/auth/me                                  │
  │  Authorization: Bearer <token>                        │
  │──────────────────────────────────────────────────────>│
  │                                                       │ 1. 校验签名/有效期
  │                                                       │ 2. 取出 sub = user_id
  │                                                       │ 3. 查 project_members
  │  { id, email, projects:[{id,name,role}] }             │
  │<──────────────────────────────────────────────────────│
```

后续每次请求都带同一个 header，后端不存 session，是无状态的。

### 2.1 两种签名方式

| 项目类型 | 算法 | 后端怎么校验 | 需要配什么 |
|---|---|---|---|
| 早期项目 / 自建 | HS256 | 用 JWT Secret 验签 | `SUPABASE_JWT_SECRET` |
| 新项目（默认） | ES256 / RS256 | 按 token 头的 `kid` 从 JWKS 取公钥 | `SUPABASE_URL` |

两种都支持，判断依据是 token 头里的 `alg`，不需要手动切换。JWKS 公钥缓存 10 分钟，`kid` 未命中时会强制刷新一次（Supabase 轮换密钥后不会立刻失效）。

### 2.2 配置

```dotenv
SUPABASE_URL=https://fhcimyjnmxqdtlpmyaxj.supabase.co
SUPABASE_JWT_SECRET=            # 只有 HS256 项目才需要填
SUPABASE_JWT_AUDIENCE=authenticated
```

`SUPABASE_URL` 和 `SUPABASE_JWT_SECRET` 至少要有一个，两个都空时所有受保护接口返回 401 并提示"服务端未配置 Supabase 鉴权"。

> **不要把 `service_role` key 配进前端。** 那个 key 能绕过所有 RLS。后端校验用的是公开的 JWT Secret 或 JWKS 公钥，不需要 service_role。

---

## 3. 接口清单

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v1/health` | 公开 | 存活探针 |
| GET | `/api/v1/ready` | 公开 | 就绪探针（含数据库连通性） |
| GET | `/api/v1/auth/me` | 登录即可 | 当前用户 + 所属项目与角色 |
| GET | `/api/v1/projects/{id}/runs` | viewer 及以上 | 最近采集记录 |
| POST | `/api/v1/projects/{id}/collect` | member 及以上 | 触发一轮采集（异步） |
| POST | `/api/v1/projects/{id}/metrics/recompute` | member 及以上 | 重算指标（不调引擎，免费） |

所有响应都是同一个信封：

```json
{ "code": "ok", "message": "success", "data": {}, "request_id": "7e4aa155" }
```

`code` 为 `ok` 表示成功，其余见第 5 节错误码。`request_id` 在排查问题时报给运维，能直接定位到那一条日志。

### 3.1 `GET /auth/me`

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
```

```json
{
  "code": "ok",
  "message": "success",
  "data": {
    "id": "3f1c...",
    "email": "qh@example.com",
    "display_name": "qh",
    "avatar_url": null,
    "is_service": false,
    "projects": [
      { "id": "812a1c97-...", "name": "演示项目 · 建筑投标软件", "brand": "智标枪", "role": "owner" }
    ]
  },
  "request_id": "…"
}
```

前端拿 `projects[].role` 决定按钮显不显示：`viewer` 隐藏"触发采集"，`member` 及以上显示。

`display_name` 取 `profiles.display_name`，没有则退到 token 里的 `user_metadata.display_name`，再没有就用邮箱前缀。**任何一个环节缺失都不报错**，避免刚注册的账号进不来。

### 3.2 `POST /projects/{id}/collect`

```bash
curl -X POST http://localhost:8000/api/v1/projects/812a1c97.../collect \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"engines": ["deepseek", "qwen"], "max_prompts": 20}'
```

入参都可选：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `engines` | string[] | 所有已配密钥的引擎 | 只跑指定引擎 |
| `max_prompts` | int (1-100) | `COLLECT_MAX_PROMPTS`（50） | 本轮最多跑几个问题 |

返回 **202**，不代表采集已完成：

```json
{
  "code": "ok",
  "data": {
    "project_id": "812a1c97-...",
    "engines": ["deepseek", "qwen"],
    "max_prompts": 20,
    "message": "采集已开始，进度可查询运行记录"
  }
}
```

**为什么是异步**：20 个问题 × 2 个引擎，串行要十几分钟，网关 60 秒就 504 了。接口只负责排队，进度让前端轮询 `/runs`。

**防重复触发**：同一项目已有 `pending` / `running` 的 run 时返回 **409**，避免同一批问题重复计费。

**限制**：后台任务跑在 API 进程内，进程重启会丢掉排队中的任务。生产上每天定时采集由 systemd timer 跑（`docs/02-deploy.md`），这个接口是给"手动补跑一次"用的，不是调度器。要多 worker 部署且需要任务可靠，得换 Celery / RQ 之类的队列。

### 3.3 `POST /projects/{id}/metrics/recompute`

规则改了（品牌别名、情绪词表）之后，用已落库的回答重算，**不调用任何引擎，不产生费用**。

```bash
curl -X POST http://localhost:8000/api/v1/projects/812a1c97.../metrics/recompute \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"days": 7}'
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `days` | int (1-90) | 7 | 重算最近几天 |
| `metric_date` | date | null | 给了就只算这一天，忽略 `days` |

```json
{ "code": "ok", "data": { "project_id": "…", "days": 7, "rows": 28 } }
```

这个接口是同步的，7 天数据通常 1 秒内返回。

### 3.4 `GET /projects/{id}/runs`

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/projects/812a1c97.../runs?limit=5"
```

```json
{
  "code": "ok",
  "data": [
    {
      "id": "…", "status": "success", "prompt_count": 5, "engine_count": 2,
      "started_at": "2026-11-21T02:00:00Z", "finished_at": "2026-11-21T02:03:12Z",
      "created_at": "2026-11-21T02:00:00Z"
    }
  ]
}
```

`status` 取值：`pending` / `running` / `success` / `partial` / `failed`。
`partial` 表示部分问题失败——看 `error` 字段（接口暂未返回，需要时再加）。

---

## 4. 角色与权限

矩阵与 `docs/05-auth-permission.md` 一致：

| 操作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 看数据、看运行记录 | ✅ | ✅ | ✅ | ✅ |
| 触发采集 | ✅ | ✅ | ✅ | ❌ |
| 重算指标 | ✅ | ✅ | ✅ | ❌ |
| 改项目信息、管成员 | ✅ | ✅ | ❌ | ❌ |

**非成员一律返回 404，不是 403。** 403 等于告诉对方"这个项目存在，只是你没权限"，项目 id 可枚举时会泄露客户名单。前端遇到 404 当"项目不存在或无权访问"处理即可。

---

## 5. 错误码

| HTTP | code | 场景 | 前端怎么处理 |
|---|---|---|---|
| 400 | `bad_request` | 没配任何引擎密钥、引擎名不存在、超出问题数上限 | 直接展示 message |
| 401 | `unauthorized` | 没带 token / token 过期 / 签名不对 / 服务端没配鉴权 | 跳登录页，重新登录 |
| 403 | `forbidden` | 是成员但角色不够（viewer 点了采集） | 提示"没有权限"，隐藏按钮 |
| 404 | `not_found` | 项目不存在，或不是成员 | 返回项目列表 |
| 409 | `conflict` | 上一轮采集还在跑 | 提示等待，可轮询 `/runs` |
| 422 | `validation_error` | 入参格式不对 | 展示 `data` 里的字段错误 |
| 500 | `internal_error` | 未捕获异常 | 提示"服务异常"，把 `request_id` 给运维 |

401 不区分"过期"和"伪造"的具体原因给外部，只给一句可读提示，避免帮攻击者探测。

---

## 6. 前端调用示例

```ts
// 1. 登录后拿 token
const { data } = await supabase.auth.signInWithPassword({ email, password })
const token = data.session!.access_token

// 2. 统一封装：所有后端请求都带上这个 header
const api = (path: string, init: RequestInit = {}) =>
  fetch(`${process.env.NEXT_PUBLIC_API_BASE}/api/v1${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  }).then(async (r) => {
    const body = await r.json()
    if (body.code !== 'ok') throw new ApiError(body.code, body.message, body.request_id)
    return body.data
  })

// 3. 进页面先确认身份与角色
const me = await api('/auth/me')
const role = me.projects.find((p) => p.id === currentProjectId)?.role
const canCollect = ['owner', 'admin', 'member'].includes(role)

// 4. 触发采集后轮询
await api(`/projects/${currentProjectId}/collect`, { method: 'POST', body: '{}' })
const timer = setInterval(async () => {
  const [latest] = await api(`/projects/${currentProjectId}/runs?limit=1`)
  if (latest.status !== 'running' && latest.status !== 'pending') clearInterval(timer)
}, 5000)
```

> token 过期时 `supabase.auth.onAuthStateChange` 会拿到新 token，封装里要读最新的，别把 token 缓存在闭包里。

---

## 7. 本地验证

```bash
# 1. 起服务
uvicorn app.main:app --reload

# 2. 打开 http://localhost:8000/docs，右上角 Authorize 填 token
#    没有 token 时可以看接口结构，但调不动受保护接口

# 3. 没有 Supabase 账号时，用测试里的自签方式造一个 HS256 token
python -c "
import jwt, time
print(jwt.encode({'sub':'11111111-1111-1111-1111-111111111111','aud':'authenticated',
  'iss':'https://test.supabase.co/auth/v1','role':'authenticated','email':'me@example.com',
  'exp':int(time.time())+3600}, 'your-jwt-secret', algorithm='HS256'))
"

# 4. 本地 SQLite 还没有 profile / project_members 数据时：
python -m app.cli init      # 建表（含 profiles / project_members）
# 再手动插一条 project_members，role 填 owner，才能触发采集
```

测试覆盖（`pytest tests/test_auth.py -q`）：

- 正常 token、过期、签名错、aud 错、iss 错、缺 sub、乱码 token、未配置；
- 角色：viewer 被拒、member 通过、非成员 404、非 UUID 的 sub 被拒；
- HTTP 层：不带 token 调 `/auth/me` 和 `/collect` 都是 401。
