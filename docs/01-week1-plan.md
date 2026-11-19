# 第一周工作计划（W1）

> 目标：跑通「登录 → 项目列表 → 项目详情 → 问题库」这条竖线。
> 不碰采集、不碰指标、不接 AI 引擎。周五能演示即过关。

## 一、本周范围

**做**

| # | 功能 | 说明 |
|---|---|---|
| 1 | 登录 | 邮箱 + 密码，签发 JWT，单一种子账号，不做注册 |
| 2 | 项目管理 | 列表（分页）、新建、查看详情 |
| 3 | 问题库 | 按项目查看问题列表、单个新增问题 |
| 4 | 前端四页 | 登录页、项目列表页、项目详情页、问题列表（详情页内） |

**不做**（写进 W2/W3，本周一行都不写）

- 采集任务、AI 引擎调用、原始回答存储
- 提及检测、指标计算、图表看板
- 批量导入、导出、定时任务
- 注册、找回密码、多租户、权限矩阵

## 二、分工与仓库

| 角色 | 负责 | 仓库 |
|---|---|---|
| 你 | 后端：接口、数据库、迁移、鉴权、部署 | 本仓库（backend），推 GitHub 私仓 |
| 学弟 | 前端：Vue 3 + Vite + TS + Element Plus | 自建 GitHub 私仓（frontend） |

两个仓库独立，**接口通过 OpenAPI 传递**（后端 `/docs` 与 `/openapi.json` 就是唯一契约）。

## 三、周一上午要拍板的 5 件事

| 事项 | 本周定法 | 理由 |
|---|---|---|
| 数据库 | 开发期 SQLite（`sqlite+aiosqlite`），上线换 Postgres | 队友零安装；换库只改连接串 |
| 本周表数量 | 只建 `users` `projects` `prompts` | 其余表 W2 再建，现在建了也没有数据 |
| 鉴权 | JWT，24 小时有效，种子账号 `admin@geo.local / admin123` | 内部工具，注册流程纯浪费 |
| 采集 | 本周零代码 | 先让 CRUD 跑通，否则两边互相等 |
| 接口风格 | 沿用框架统一信封 `{code, message, data, request_id}` | 前端只判一处 |

## 四、环境准备（周日上午，1 小时）

### 后端（项目根目录执行）

```bash
.venv/Scripts/python.exe -m pip install sqlalchemy aiosqlite alembic "pwdlib[argon2]" pyjwt asyncpg
```

同步写进 `pyproject.toml`，否则 CI 装不到依赖：

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "sqlalchemy>=2.0",
    "aiosqlite>=0.20",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pwdlib[argon2]>=0.2",
    "pyjwt>=2.8",
]
```

### 前端（学弟执行）

```bash
node -v          # 需 20 LTS 以上
npm i -g pnpm
pnpm create vite frontend --template vue-ts
cd frontend && pnpm add vue-router pinia axios element-plus echarts dayjs
```

## 五、接口契约（本周只这 7 个）

统一前缀 `/api/v1`。除登录外都要带 `Authorization: Bearer <token>`。

> **心法**：不要另写接口文档。先写出「返回假数据的真接口」，`/docs` 就是活文档，学弟能点着试；之后把假数据换成真查询，形状不变。

### 1. 登录

```
POST /api/v1/auth/login
```

```jsonc
// 请求
{ "email": "admin@geo.local", "password": "admin123" }

// 响应 200
{ "code": "ok", "message": "success", "request_id": "a1b2c3",
  "data": {
    "access_token": "eyJhbGciOi...",
    "token_type": "bearer",
    "expires_in": 86400,
    "user": { "id": "u_1", "name": "管理员", "email": "admin@geo.local" }
  } }

// 响应 401
{ "code": "unauthorized", "message": "邮箱或密码错误", "data": null, "request_id": "a1b2c3" }
```

### 2. 当前用户

```
GET /api/v1/auth/me
```

```jsonc
{ "code": "ok", "message": "success", "data": { "id": "u_1", "name": "管理员", "email": "admin@geo.local" }, "request_id": "..." }
```

### 3. 项目列表

```
GET /api/v1/projects?page=1&page_size=20
```

```jsonc
{ "code": "ok", "message": "success",
  "data": {
    "items": [
      { "id": "p_1", "name": "小红书 GEO 监测", "brand": "小红书",
        "aliases": ["小红书", "RED", "xiaohongshu"],
        "competitors": ["抖音", "B站"],
        "prompt_count": 10, "created_at": "2026-11-19T09:00:00Z" }
    ],
    "meta": { "page": 1, "page_size": 20, "total": 1 }
  },
  "request_id": "..." }
```

### 4. 新建项目

```
POST /api/v1/projects
```

```jsonc
// 请求
{ "name": "小红书 GEO 监测", "brand": "小红书",
  "aliases": ["小红书", "RED"], "competitors": ["抖音", "B站"] }

// 响应 201，data 同列表单项
```

### 5. 项目详情

```
GET /api/v1/projects/{project_id}
```

响应 `data` 结构同上；不存在返回 404 `{"code":"not_found"}`。

### 6. 问题列表

```
GET /api/v1/projects/{project_id}/prompts?page=1&page_size=50
```

```jsonc
{ "code": "ok", "message": "success",
  "data": { "items": [
      { "id": "q_1", "text": "小红书和抖音哪个更适合做美妆种草？",
        "category": "竞品对比", "enabled": true, "created_at": "2026-11-19T09:00:00Z" }
    ], "meta": { "page": 1, "page_size": 50, "total": 1 } },
  "request_id": "..." }
```

### 7. 新增问题

```
POST /api/v1/projects/{project_id}/prompts
```

```jsonc
// 请求
{ "text": "小红书如何提升笔记曝光？", "category": "平台玩法" }
// 响应 201，data 同上单项
```

## 六、表结构（只建 3 张）

`aliases` 与 `competitors` 用 JSON 列存数组，是 W3 指标计算的全部输入，现在定好后面不改表。

```python
# app/models/user.py
class User(Base):
    __tablename__ = "users"
    id: str = mapped_column(String(32), primary_key=True)          # uuid4().hex
    email: str = mapped_column(String(255), unique=True, index=True)
    name: str = mapped_column(String(64))
    password_hash: str = mapped_column(String(255))
    created_at: datetime = mapped_column(DateTime(timezone=True))

# app/models/project.py
class Project(Base):
    __tablename__ = "projects"
    id: str = mapped_column(String(32), primary_key=True)
    owner_id: str = mapped_column(ForeignKey("users.id"), index=True)
    name: str = mapped_column(String(100))
    brand: str = mapped_column(String(100))
    aliases: list[str] = mapped_column(JSON, default=list)
    competitors: list[str] = mapped_column(JSON, default=list)
    created_at: datetime = mapped_column(DateTime(timezone=True))

# app/models/prompt.py
class Prompt(Base):
    __tablename__ = "prompts"
    id: str = mapped_column(String(32), primary_key=True)
    project_id: str = mapped_column(ForeignKey("projects.id"), index=True)
    text: str = mapped_column(Text)
    category: str = mapped_column(String(32), default="通用")
    enabled: bool = mapped_column(Boolean, default=True)
    created_at: datetime = mapped_column(DateTime(timezone=True))
```

## 七、按天任务（每天约 3 小时）

### 周一：定契约

**你**

1. `app/schemas/` 下新增 `auth.py`、`project.py`、`prompt.py`（入参出参分开）
2. `app/api/v1/endpoints/` 下新增 `auth.py`、`projects.py`、`prompts.py`，先返回硬编码假数据
3. `router.py` 挂载三个模块
4. 启动 `uvicorn app.main:app --reload`，在 `/docs` 里逐个点通 7 个接口
5. 把 `http://<你内网IP>:8000` 和 `/docs` 地址发给学弟

**学弟**

1. 建 Vite 项目，配 `vue-router` 三个路由、`pinia` 一个 user store
2. 封装 `src/api/request.ts`：
   - `baseURL = import.meta.env.VITE_API_BASE`
   - 请求拦截器自动加 `Authorization: Bearer ${token}`
   - 响应拦截器：`code === 'ok'` 直接返回 `data`，否则 `ElMessage.error(message)` 并抛错
   - 401 清 token 跳登录页
3. 三个页面先写静态骨架

**验收**：学弟能从 `/docs` 抄到真实响应结构，页面能跑（数据是假的）

### 周二：数据层

**你**

```bash
# 1. 建连接层 app/core/db.py（async engine + session 工厂 + get_session 依赖）
# 2. 建三个模型文件 app/models/
# 3. 初始化迁移
.venv/Scripts/python.exe -m alembic init -t async migrations
# 4. 改 alembic.ini 的 sqlalchemy.url 与 migrations/env.py 的 target_metadata
# 5. 生成并执行首个迁移
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "init users projects prompts"
.venv/Scripts/python.exe -m alembic upgrade head
# 6. 写 seed 脚本 app/scripts/seed.py
.venv/Scripts/python.exe -m app.scripts.seed
```

`.env` 里加：

```
DATABASE_URL=sqlite+aiosqlite:///./data/geo.db
JWT_SECRET=dev-only-change-me
```

`data/` 目录加入 `.gitignore`。

**学弟**：登录页（表单校验、调登录接口、token 存 localStorage、跳转列表页）

**验收**：`data/geo.db` 存在，库里有 1 个用户、2 个项目、10 个问题

### 周三：鉴权

**你**

1. `app/core/security.py`：`hash_password` / `verify_password`（argon2）、`create_access_token` / `decode_token`（pyjwt，HS256，`exp`=24h）
2. `app/api/deps.py` 加 `get_current_user`：解析 Bearer，失败抛 `UnauthorizedError`
3. `auth/login` 改成查库校验密码；`auth/me` 返回当前用户
4. 补测试：登录成功、密码错误 401、无 token 401

**学弟**：项目列表页（表格 + 分页 + 新建弹窗 + 表单校验），数据先用 mock

**验收**：`pytest` 全绿；用 curl 或 `/docs` 能拿到 token 并访问 `/auth/me`

### 周四：CRUD + 联调

**你**

1. `projects` 三个接口落真库（列表按 `owner_id` 过滤，`prompt_count` 用子查询或 count）
2. `prompts` 两个接口落真库
3. 项目不存在抛 `NotFoundError`，不要自己拼响应
4. `.env` 配 CORS：`CORS_ORIGINS=http://localhost:5173,http://<你内网IP>:5173`
5. 补测试：项目列表 200、详情 404、问题列表 200

**学弟**：项目详情页 + 问题列表接真接口，关掉 mock

**验收**：前端关掉 mock，登录 → 列表 → 详情 → 问题，全流程不报错

### 周五：收口 + 演示

**你**

1. 修联调暴露的问题（优先修接口，不改契约）
2. 补 `README.md` 的启动说明（两条命令：装依赖、跑服务）
3. 写 W2 预告：`runs` / `responses` 表结构 + 采集接口形状
4. 合并 PR，确认 CI 绿

**学弟**

1. 空态、加载中、接口报错的提示
2. 准备 3 分钟演示脚本：登录 → 项目列表 → 新建 → 进详情 → 看问题

**验收**：从零启动能跑通；演示一次不断链

## 八、联调必踩的三个坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 浏览器报 CORS | `.env` 没配前端地址，或配了 `*` 又开 credentials | `CORS_ORIGINS` 写具体地址，英文逗号分隔 |
| 除了登录全是 401 | axios 只给登录接口发了 token | 拦截器统一加，登录接口排除 |
| 时间显示差 8 小时 | 后端存 UTC，前端直接显示 | 后端只管 UTC，前端 `dayjs` 转本地 |

## 九、周五交付清单

- [ ] 7 个接口在 `/docs` 里全部可点通
- [ ] 3 张表建好，Alembic 有首个迁移，`alembic upgrade head` 可重复执行
- [ ] seed 脚本一条命令灌出可演示数据
- [ ] `pytest` 至少 6 个用例（登录 3 + 项目 2 + 问题 1），CI 绿
- [ ] 前端四页可跑，README 写清启动命令
- [ ] W2 接口预告已发出（`POST /projects/{id}/runs`、`GET /runs/{id}`、`GET /responses?run_id=`）

## 十、给学弟的页面拆解（他照着做）

| 页面 | 路由 | 数据来源 | 关键交互 |
|---|---|---|---|
| 登录 | `/login` | `POST /auth/login` | 表单校验、错误提示、成功后跳 `/projects` |
| 项目列表 | `/projects` | `GET /projects` | 表格分页、新建弹窗、点行进详情 |
| 项目详情 | `/projects/:id` | `GET /projects/{id}` | 展示品牌/别名/竞品，Tab 切换到问题 |
| 问题列表 | 同上内嵌 | `GET /projects/{id}/prompts` | 列表、新增弹窗、启用状态标记 |

统一约定：
- 所有请求走 `src/api/request.ts`，不要在组件里直接 `axios.get`
- 接口地址集中放 `src/api/*.ts`，一个模块一个文件
- 组件里不写 `if (res.code === 'ok')`，拦截器已经解包
- 环境变量 `VITE_API_BASE=http://localhost:8000/api/v1`，打包时改成线上域名
