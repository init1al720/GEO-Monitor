# 第一周工作计划（W1）

> 目标：跑通「登录 → 项目列表 → 项目详情 → 问题库」这条竖线。
> 不碰采集、不碰指标、不接 AI 引擎。周五能演示即过关。
>
> 先看 [00-basics.md](./00-basics.md) 搞懂 CI / JWT / OpenAPI / 迁移，再看这份。

---

## 〇、本周结束时是什么样子

周五下午，前端打开 `http://localhost:5173`：

1. 出现登录页，输入 `admin@geo.local` / `admin123`
2. 跳转到项目列表，看到 2 条项目（种子数据），有分页
3. 点「新建项目」，填名称、品牌、别名、竞品，提交后列表多出一条
4. 点其中一条，进入详情页，看到品牌名、别名标签、竞品标签
5. 切到「问题库」Tab，看到 5 条问题，能新增一条
6. 刷新页面，登录态还在，不用重新登录
7. 手动把地址改成 `/projects/p_1` 直接访问，不报错

后端打开 `http://localhost:8000/docs`，以上操作对应的 7 个接口都在，能点着试。

**这就是 W1 的全部意义：把"两个人能不能顺畅对接"这件事验证掉。** 业务复杂度是刻意压到最低的。

---

## 一、本周范围

### 做

| # | 功能 | 说明 | 涉及接口 |
|---|---|---|---|
| 1 | 登录 | 邮箱 + 密码，签发 JWT，单一种子账号，不做注册 | `POST /auth/login` |
| 2 | 当前用户 | 前端刷新页面时用它判断登录态是否有效 | `GET /auth/me` |
| 3 | 项目列表 | 分页，按当前用户过滤 | `GET /projects` |
| 4 | 新建项目 | 名称、品牌、别名数组、竞品数组 | `POST /projects` |
| 5 | 项目详情 | 单条详情 | `GET /projects/{id}` |
| 6 | 问题列表 | 按项目查，分页 | `GET /projects/{id}/prompts` |
| 7 | 新增问题 | 文本 + 分类 | `POST /projects/{id}/prompts` |

### 不做（本周一行代码都别写）

- ❌ 采集任务、AI 引擎调用、原始回答存储 → W2
- ❌ 提及检测、指标计算、图表看板 → W3
- ❌ 批量导入、Excel 导出、定时任务 → W2/W4
- ❌ 注册、找回密码、多租户、权限矩阵、操作日志 → 第二阶段
- ❌ 头像上传、主题切换、国际化、暗色模式 → 永远别在第一周做

> 为什么这么克制：两个人第一次合作，最容易死在"想做的太多，结果一样都没跑通"。第一周只要证明"能对接"，后面加功能就是流水线。

---

## 二、角色分工

| 角色 | 负责范围 | 仓库 | 交付物 |
|---|---|---|---|
| 后端 | 接口、数据库、鉴权、迁移、部署 | `geo-backend`（本仓库） | 7 个可用接口 + `/docs` |
| 前端 | 页面、交互、请求封装 | `geo-frontend`（自建） | 4 个可用页面 |

**边界三条铁律：**

1. 前端不碰业务规则（比如"项目名不能重复"由后端判断并返回错误，前端只负责展示）
2. 后端不写页面逻辑（不返回 HTML、不返回"该显示什么颜色"这类信息）
3. 接口字段只有后端能改，改之前先在群里说，改完必须让 `/docs` 同步

---

## 三、周一上午要拍板的 5 件事（30 分钟，别拖）

| 事项 | 本周定法 | 为什么这么定 |
|---|---|---|
| 数据库 | 开发期 SQLite（`sqlite+aiosqlite:///./data/geo.db`），上线换 Postgres | 两台电脑都不用装数据库软件，开箱即用；切 Postgres 只改一个连接串 |
| 本周表数量 | 只建 `users` `projects` `prompts` 三张 | `runs`/`responses` 是 W2 的事，现在建了也没数据，反而要维护 |
| 鉴权 | JWT，24 小时有效，种子账号 `admin@geo.local / admin123` | 内部工具，做注册流程是纯浪费；种子账号写死在脚本里，谁拉代码都能跑 |
| 采集 | 本周零代码 | 采集依赖引擎 API key、限流、异步任务，会把 W1 拖垮 |
| 接口风格 | 统一信封 `{code, message, data, request_id}`（框架已实现，直接用 `success()`） | 前端只判 `code` 一处，出错时能拿 `request_id` 找日志 |

另一个约定：**所有时间字段用 UTC，ISO 8601 带 Z**，例如 `2026-11-19T09:00:00Z`。前端用 `dayjs` 转本地时区显示，后端不做时区转换。

---

## 四、环境准备（周日上午，1 小时）

### 4.1 后端：装新依赖

在项目根目录执行：

```bash
.venv/Scripts/python.exe -m pip install sqlalchemy aiosqlite alembic "pwdlib[argon2]" pyjwt asyncpg email-validator
```

**验证**（每条都要有输出，报错就是没装上）：

```bash
.venv/Scripts/python.exe -c "import sqlalchemy, alembic, jwt, aiosqlite; print(sqlalchemy.__version__)"
# 预期：2.0.x

.venv/Scripts/python.exe -c "from pwdlib import PasswordHash; print(PasswordHash.recommended())"
# 预期：<pwdlib.hashers.argon2.Argon2Hasher object at 0x...>
```

**同步写进 `pyproject.toml`**（不写进去，CI 装不到依赖会直接红）：

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "email-validator>=2.1",
    "sqlalchemy>=2.0",
    "aiosqlite>=0.20",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pwdlib[argon2]>=0.2",
    "pyjwt>=2.8",
]
```

改完确认没写坏：

```bash
.venv/Scripts/python.exe -m pip install -e ".[dev]" && .venv/Scripts/python.exe -m pytest -q
# 预期：8 passed
```

### 4.2 前端：脚手架与依赖

```bash
node -v                    # 需要 v20.x 或更高，低于 20 去官网装 LTS
npm i -g pnpm
pnpm -v                    # 预期 9.x
```

新建项目（**不要在 `geo-backend` 目录里建**）：

```bash
pnpm create vite geo-frontend --template vue-ts
cd geo-frontend
pnpm install
pnpm add vue-router pinia axios element-plus echarts dayjs
pnpm dev
# 预期：浏览器打开 http://localhost:5173 看到 Vite 默认页
```

如果 `pnpm create` 卡住不动：换 `npm create vite@latest geo-frontend -- --template vue-ts`，之后用 npm 装依赖。

`vite.config.ts` 里配一个别名，后面 import 用 `@/`：

```ts
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
})
```

### 4.3 双方互相能访问

- 后端启动加 `--host 0.0.0.0`，前端才能用局域网 IP 连
- 前端启动加 `--host`，后端也能从自己电脑打开页面
- 连不上先查 Windows 防火墙是否拦了端口

---

## 五、接口契约（本周只有这 7 个）

### 5.1 总则

| 项 | 约定 |
|---|---|
| 前缀 | `/api/v1` |
| 鉴权 | 除 `login` 外都要带 `Authorization: Bearer <token>` |
| 响应 | 统一信封 `{code, message, data, request_id}`，`code=ok` 表示成功 |
| 分页 | 入参 `page`（从 1 开始）、`page_size`（默认 20，最大 200）；出参 `data.items` + `data.meta` |
| 时间 | UTC，ISO 8601 带 `Z` |
| ID | 32 位 hex 字符串（`uuid.uuid4().hex`），不是自增数字 |
| 字段命名 | 全小写下划线（`created_at`、`prompt_count`），不用驼峰 |

### 5.2 错误码对照表（前端照着写提示）

| HTTP | code | 什么时候出现 | 前端怎么处理 |
|---|---|---|---|
| 200/201 | `ok` | 成功 | 正常展示 |
| 401 | `unauthorized` | 没带 token / 过期 / 密码错 | 密码错要提示"邮箱或密码错误"；其余清 token 跳登录页 |
| 403 | `forbidden` | 无权访问别人的资源 | 提示"没有权限" |
| 404 | `not_found` | 项目/问题不存在 | 提示"资源不存在"，回列表页 |
| 422 | `validation_error` | 字段校验失败（`data` 里是逐字段错误） | 表单字段下方飘红 |
| 500 | `internal_error` | 后端崩了 | 提示"服务异常"，把 `request_id` 给后端查日志 |

### 5.3 接口详情

#### ① 登录

```
POST /api/v1/auth/login
Content-Type: application/json
```

请求：

```json
{ "email": "admin@geo.local", "password": "admin123" }
```

成功 200：

```json
{
  "code": "ok",
  "message": "success",
  "request_id": "a1b2c3d4",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 86400,
    "user": { "id": "u_1", "name": "管理员", "email": "admin@geo.local" }
  }
}
```

失败 401：

```json
{ "code": "unauthorized", "message": "邮箱或密码错误", "data": null, "request_id": "a1b2c3d4" }
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `access_token` | string | 后续请求放在 `Authorization: Bearer ` 后面 |
| `token_type` | string | 固定 `bearer`，前端可忽略 |
| `expires_in` | int | 有效期秒数（86400 = 24 小时） |
| `user` | object | 当前用户，前端存 store 里显示名字 |

#### ② 当前用户

```
GET /api/v1/auth/me
Authorization: Bearer <token>
```

成功 200 的 `data`：

```json
{ "id": "u_1", "name": "管理员", "email": "admin@geo.local" }
```

用途：前端刷新页面后，用 localStorage 里的 token 调一次。200 说明有效直接进主页；401 说明过期，跳登录页。

#### ③ 项目列表

```
GET /api/v1/projects?page=1&page_size=20
```

成功 200：

```json
{
  "code": "ok",
  "message": "success",
  "request_id": "a1b2c3d4",
  "data": {
    "items": [
      {
        "id": "p_1",
        "name": "小红书 GEO 监测",
        "brand": "小红书",
        "aliases": ["小红书", "RED", "xiaohongshu"],
        "competitors": ["抖音", "B站"],
        "prompt_count": 3,
        "created_at": "2026-11-19T09:00:00Z"
      }
    ],
    "meta": { "page": 1, "page_size": 20, "total": 1 }
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `aliases` | string[] | 品牌别名，W3 判定"品牌是否被提及"时用 |
| `competitors` | string[] | 竞品名，W3 算声量份额时用 |
| `prompt_count` | int | 该项目下问题数量，列表页展示"N 个问题" |
| `meta.total` | int | 总条数，前端算总页数 |

#### ④ 新建项目

```
POST /api/v1/projects
Content-Type: application/json
```

请求：

```json
{
  "name": "小红书 GEO 监测",
  "brand": "小红书",
  "aliases": ["小红书", "RED"],
  "competitors": ["抖音", "B站"]
}
```

校验规则（后端负责，前端同步做一遍提示）：

| 字段 | 规则 | 不满足时的提示 |
|---|---|---|
| `name` | 1–100 字符，必填 | 请输入项目名称 |
| `brand` | 1–100 字符，必填 | 请输入品牌名 |
| `aliases` | 数组，每项 1–50 字符，可为空 | — |
| `competitors` | 数组，可为空 | — |

成功 201：`data` 结构与列表单项一致。

#### ⑤ 项目详情

```
GET /api/v1/projects/{project_id}
```

成功 200 的 `data` 与列表单项一致；不存在时 404 `{"code":"not_found","message":"项目不存在"}`。

#### ⑥ 问题列表

```
GET /api/v1/projects/{project_id}/prompts?page=1&page_size=50
```

成功 200：

```json
{
  "code": "ok", "message": "success", "request_id": "a1b2c3d4",
  "data": {
    "items": [
      {
        "id": "q_1",
        "text": "小红书和抖音哪个更适合做美妆种草？",
        "category": "竞品对比",
        "enabled": true,
        "created_at": "2026-11-19T09:00:00Z"
      }
    ],
    "meta": { "page": 1, "page_size": 50, "total": 1 }
  }
}
```

> 为什么留 `enabled`：W2 采集只跑启用的问题，停用的不消耗引擎额度。现在建好，W2 不用改表。

#### ⑦ 新增问题

```
POST /api/v1/projects/{project_id}/prompts
```

请求：

```json
{ "text": "小红书如何提升笔记曝光？", "category": "平台玩法" }
```

`text` 必填 1–500 字符；`category` 可选，默认 `"通用"`。成功 201，`data` 同上单项。

---

## 六、数据库（3 张表）

### 6.1 第一步：配置与连接层

**改 `app/core/config.py`**，在 `Settings` 里加：

```python
    # 数据库
    database_url: str = "sqlite+aiosqlite:///./data/geo.db"
    # 鉴权
    jwt_secret: str = "dev-only-change-me"
    jwt_expire_hours: int = 24
```

**新建 `app/core/db.py`**：

```python
"""数据库连接层：async engine + session 工厂。

约定：
- 所有 DB 操作都在 repositories / services 里通过 session 完成
- 一个请求一个 session，请求结束自动关闭
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类，Alembic 靠它发现表结构。"""


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求拿一个 session，请求结束自动关闭。"""
    async with SessionLocal() as session:
        yield session
```

顺手建 `data/` 目录（SQLite 文件放这里，已在 `.gitignore` 中忽略）。

### 6.2 第二步：三个模型

**`app/models/user.py`**：

```python
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

**`app/models/project.py`**：

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    brand: Mapped[str] = mapped_column(String(100))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    competitors: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

**`app/models/prompt.py`**：

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="通用")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

**改 `app/models/__init__.py`**（关键！Alembic 只发现这里 import 过的模型）：

```python
from app.core.db import Base
from app.models.prompt import Prompt
from app.models.project import Project
from app.models.user import User

__all__ = ["Base", "Prompt", "Project", "User"]
```

### 6.3 第三步：Alembic

```bash
# 1. 初始化（-t async 必须加，否则跑不了异步连接）
.venv/Scripts/python.exe -m alembic init -t async migrations
```

**改 `alembic.ini`**：`sqlalchemy.url` 留空（改由代码读配置）：

```ini
sqlalchemy.url =
```

**改 `migrations/env.py`**，在文件顶部 import 区加：

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings          # noqa: E402
from app.models import Base                       # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

> 原来的 `env.py` 里有一行 `target_metadata = None`，删掉，用上面这行替代。

```bash
# 2. 生成迁移脚本
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "init users projects prompts"

# 3. 打开 migrations/versions/xxx_init_xxx.py 看一眼
#    应该有 create_table('users')、('projects')、('prompts') 三段
#    没有就说明 models/__init__.py 没 import 到，回头检查

# 4. 执行
.venv/Scripts/python.exe -m alembic upgrade head

# 5. 验证
.venv/Scripts/python.exe -c "import sqlite3; print(sqlite3.connect('data/geo.db').execute(\"select name from sqlite_master where type='table'\").fetchall())"
# 预期：包含 users / projects / prompts / alembic_version
```

### 6.4 第四步：种子数据

**新建 `app/scripts/__init__.py`**（空文件）与 **`app/scripts/seed.py`**：

```python
"""灌演示数据：1 个用户 + 2 个项目 + 5 个问题。

可重复执行：已存在的记录跳过，不会灌重复。
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import Project, Prompt, User

ADMIN_EMAIL = "admin@geo.local"
ADMIN_PASSWORD = "admin123"

PROJECTS = [
    {
        "name": "小红书 GEO 监测",
        "brand": "小红书",
        "aliases": ["小红书", "RED", "xiaohongshu"],
        "competitors": ["抖音", "B站"],
        "prompts": [
            ("小红书和抖音哪个更适合做美妆种草？", "竞品对比"),
            ("小红书如何提升笔记曝光？", "平台玩法"),
            ("小红书上的带货转化率怎么样？", "商业化"),
        ],
    },
    {
        "name": "ExampleCRM GEO 监测",
        "brand": "ExampleCRM",
        "aliases": ["ExampleCRM", "示例 CRM"],
        "competitors": ["Salesforce", "HubSpot"],
        "prompts": [
            ("有哪些适合中小企业的 CRM？", "选型推荐"),
            ("国内 CRM 和国外 CRM 有什么区别？", "竞品对比"),
        ],
    },
]


async def main() -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if user is None:
            user = User(
                id=uuid.uuid4().hex,
                email=ADMIN_EMAIL,
                name="管理员",
                password_hash=hash_password(ADMIN_PASSWORD),
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.flush()

        for item in PROJECTS:
            exists = await session.scalar(select(Project).where(Project.name == item["name"]))
            if exists is not None:
                continue
            project = Project(
                id=uuid.uuid4().hex,
                owner_id=user.id,
                name=item["name"],
                brand=item["brand"],
                aliases=item["aliases"],
                competitors=item["competitors"],
                created_at=datetime.now(UTC),
            )
            session.add(project)
            await session.flush()
            for text, category in item["prompts"]:
                session.add(
                    Prompt(
                        id=uuid.uuid4().hex,
                        project_id=project.id,
                        text=text,
                        category=category,
                        enabled=True,
                        created_at=datetime.now(UTC),
                    )
                )

        await session.commit()
    print("seed 完成：", ADMIN_EMAIL, "/", ADMIN_PASSWORD)


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
.venv/Scripts/python.exe -m app.scripts.seed
# 预期：seed 完成： admin@geo.local / admin123
```

> `hash_password` 周三才写。周二可以先把 import 换成 `def hash_password(p): return p`（顶在文件里临时占位），周三换回真函数；最省事的做法是周三删掉 `data/geo.db` 重新迁移 + 重跑 seed。

---

## 七、逐日任务

### 周一：定契约（不写数据库，先让接口"存在"）

目的：让前端立刻能开始画页面，不用等你写完数据库。

#### 后端（约 3 小时）

**1. 加配置**（10 分钟）
改 `app/core/config.py`，加 `database_url`、`jwt_secret`、`jwt_expire_hours`。

**2. 三个 schema 文件**（40 分钟）

`app/schemas/auth.py`：

```python
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
```

`app/schemas/project.py`：

```python
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    brand: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)


class ProjectRead(BaseModel):
    id: str
    name: str
    brand: str
    aliases: list[str]
    competitors: list[str]
    prompt_count: int = 0
    created_at: datetime
```

`app/schemas/prompt.py`：

```python
from datetime import datetime

from pydantic import BaseModel, Field


class PromptCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    category: str = Field(default="通用", max_length=32)


class PromptRead(BaseModel):
    id: str
    text: str
    category: str
    enabled: bool
    created_at: datetime
```

**3. 三个 endpoint 文件，先返回假数据**（60 分钟）

`app/api/v1/endpoints/auth.py`：

```python
from fastapi import APIRouter

from app.core.response import success
from app.schemas.auth import LoginRequest

router = APIRouter(prefix="/auth", tags=["鉴权"])


@router.post("/login", summary="登录")
async def login(payload: LoginRequest):
    # TODO 周三改成查库校验密码
    return success(
        {
            "access_token": "fake-token-for-frontend",
            "token_type": "bearer",
            "expires_in": 86400,
            "user": {"id": "u_1", "name": "管理员", "email": payload.email},
        }
    )


@router.get("/me", summary="当前用户")
async def me():
    # TODO 周三改成从 token 解析
    return success({"id": "u_1", "name": "管理员", "email": "admin@geo.local"})
```

`app/api/v1/endpoints/projects.py`：

```python
from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.response import ApiResponse, success
from app.schemas.common import PageData, PageMeta, PageQuery
from app.schemas.project import ProjectCreate, ProjectRead

router = APIRouter(prefix="/projects", tags=["项目"])

FAKE = ProjectRead(
    id="p_1",
    name="小红书 GEO 监测",
    brand="小红书",
    aliases=["小红书", "RED"],
    competitors=["抖音", "B站"],
    prompt_count=3,
    created_at=datetime.now(UTC),
)


@router.get("", response_model=ApiResponse[PageData[ProjectRead]], summary="项目列表")
async def list_projects(page: int = 1, page_size: int = 20):
    query = PageQuery(page=page, page_size=page_size)
    return success(PageData[ProjectRead](items=[FAKE], meta=PageMeta.build(query, 1)))


@router.post(
    "",
    response_model=ApiResponse[ProjectRead],
    status_code=201,
    summary="新建项目",
)
async def create_project(payload: ProjectCreate):
    return success(FAKE, message="创建成功")


@router.get("/{project_id}", response_model=ApiResponse[ProjectRead], summary="项目详情")
async def get_project(project_id: str):
    return success(FAKE)
```

`app/api/v1/endpoints/prompts.py`：

```python
from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.response import ApiResponse, success
from app.schemas.common import PageData, PageMeta, PageQuery
from app.schemas.prompt import PromptCreate, PromptRead

router = APIRouter(tags=["问题库"])

FAKE = PromptRead(
    id="q_1",
    text="小红书和抖音哪个更适合做美妆种草？",
    category="竞品对比",
    enabled=True,
    created_at=datetime.now(UTC),
)


@router.get(
    "/projects/{project_id}/prompts",
    response_model=ApiResponse[PageData[PromptRead]],
    summary="问题列表",
)
async def list_prompts(project_id: str, page: int = 1, page_size: int = 50):
    query = PageQuery(page=page, page_size=page_size)
    return success(PageData[PromptRead](items=[FAKE], meta=PageMeta.build(query, 1)))


@router.post(
    "/projects/{project_id}/prompts",
    response_model=ApiResponse[PromptRead],
    status_code=201,
    summary="新增问题",
)
async def create_prompt(project_id: str, payload: PromptCreate):
    return success(FAKE, message="创建成功")
```

**4. 挂路由**（5 分钟）

`app/api/v1/router.py`：

```python
from app.api.v1.endpoints import auth, example, health, projects, prompts

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(prompts.router)
```

> `example` 模块可以留到周五再删（删的时候同步删 `services/example.py`、`schemas/example.py`、`deps.py` 里的 `ExampleServiceDep`、`tests/test_example.py`）。

**5. 在 `/docs` 里点一遍**（30 分钟）

```bash
uvicorn app.main:app --reload --host 0.0.0.0
# 打开 http://127.0.0.1:8000/docs
```

逐个点开 7 个接口，确认参数能填、Execute 有响应、结构和你写的一致。

**6. 把下面这段发给前端**（5 分钟）

```
后端地址：http://192.168.1.10:8000
接口文档：http://192.168.1.10:8000/docs
登录账号：admin@geo.local / admin123（现在是假的，任意密码都过）
鉴权方式：Authorization: Bearer <token>
```

#### 前端（约 3 小时）

**1. 目录结构**（30 分钟）

```
src/
├─ api/
│  ├─ request.ts        # axios 实例 + 拦截器（核心）
│  ├─ auth.ts           # login() / getMe()
│  ├─ project.ts        # listProjects() / createProject() / getProject()
│  └─ prompt.ts         # listPrompts() / createPrompt()
├─ router/index.ts
├─ stores/user.ts
├─ layouts/BasicLayout.vue
├─ views/
│  ├─ LoginView.vue
│  ├─ ProjectListView.vue
│  └─ ProjectDetailView.vue
├─ components/PromptTable.vue
├─ App.vue
└─ main.ts
```

**2. `src/api/request.ts`**（40 分钟，本周最重要的一段）

```ts
import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1',
  timeout: 30000,
})

// 请求拦截：自动带 token
request.interceptors.request.use((config) => {
  const token = localStorage.getItem('geo_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：解包信封 + 统一报错
request.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body.code === 'ok') return body.data
    ElMessage.error(body.message || '请求失败')
    return Promise.reject(new Error(body.message))
  },
  (error) => {
    const status = error.response?.status
    const body = error.response?.data
    if (status === 401) {
      if (!location.pathname.includes('/login')) {
        localStorage.removeItem('geo_token')
        router.push('/login')
      }
      ElMessage.error(body?.message || '登录已过期')
    } else {
      ElMessage.error(body?.message || '网络异常，请稍后重试')
    }
    return Promise.reject(error)
  },
)

export default request
```

**3. 路由与守卫**（30 分钟）

```ts
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/LoginView.vue') },
    {
      path: '/',
      component: () => import('@/layouts/BasicLayout.vue'),
      children: [
        { path: 'projects', component: () => import('@/views/ProjectListView.vue') },
        { path: 'projects/:id', component: () => import('@/views/ProjectDetailView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/projects' },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('geo_token')
  if (!token && to.path !== '/login') return '/login'
  if (token && to.path === '/login') return '/projects'
})

export default router
```

**4. 环境变量**（5 分钟）

`.env.development`：

```
VITE_API_BASE=http://localhost:8000/api/v1
```

用局域网 IP 联调时改成 `http://192.168.1.10:8000/api/v1`，改完重启 `pnpm dev`。

**5. 三个页面骨架**（60 分钟）
- 登录页：表单 + 校验 + 提交（先 `console.log`）
- 列表页：`el-table` 假数据两行 + `el-pagination`
- 详情页：标题 + `el-descriptions` + `el-tabs`

#### 周一验收

- [ ] `/docs` 里 7 个接口全部可点通，结构与第五节一致
- [ ] 前端三个页面能打开、能互相跳转、不带 token 会被踢回登录页
- [ ] 双方互相能访问对方的地址

---

### 周二：数据库落地

#### 后端（约 3.5 小时）

1. **建 `app/core/db.py`**（见 6.1）
2. **三个模型 + 改 `models/__init__.py`**（见 6.2）
3. **Alembic 初始化与迁移四步**（见 6.3）
4. **seed 脚本跑通**（见 6.4）
5. **验证数据在库里**：

```bash
.venv/Scripts/python.exe -c "
import sqlite3
con = sqlite3.connect('data/geo.db')
print('用户:', con.execute('select email from users').fetchall())
print('项目:', con.execute('select name from projects').fetchall())
print('问题数:', con.execute('select count(*) from prompts').fetchone())
"
# 预期：1 条用户、2 条项目、5 条问题
```

#### 前端（约 3 小时）

**登录页完整实现**：

```vue
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '@/api/auth'

const router = useRouter()
const loading = ref(false)
const form = reactive({ email: 'admin@geo.local', password: 'admin123' })

async function onSubmit() {
  if (!form.email || !form.password) return ElMessage.warning('请填写邮箱和密码')
  loading.value = true
  try {
    const data = await login(form)
    localStorage.setItem('geo_token', data.access_token)
    localStorage.setItem('geo_user', JSON.stringify(data.user))
    router.push('/projects')
  } finally {
    loading.value = false
  }
}
</script>
```

要点：
- `el-form` + `rules` 做前端校验（必填、邮箱格式）
- 按钮 loading 防重复提交
- 成功存 token + user，跳 `/projects`
- 失败不用自己弹提示，拦截器已经弹过

#### 周二验收

- [ ] `data/geo.db` 存在，库里 1 用户 / 2 项目 / 5 问题
- [ ] 前端能提交登录表单并跳到列表页（此时后端还返回假 token，正常）

---

### 周三：真正的鉴权

#### 后端（约 3.5 小时）

**1. `app/core/security.py`**：

```python
"""密码哈希与 JWT 签发校验。"""

from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

_password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"


def hash_password(raw: str) -> str:
    return _password_hash.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _password_hash.verify(raw, hashed)


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    """返回 user_id，失败统一抛 401。"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("无效的登录凭证") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("无效的登录凭证")
    return user_id
```

**2. `app/api/deps.py` 加当前用户依赖**：

```python
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_token
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    if credentials is None:
        raise UnauthorizedError("未登录")
    user_id = decode_token(credentials.credentials)
    user = await session.get(User, user_id)
    if user is None:
        raise UnauthorizedError("用户不存在")
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
```

**3. `auth.py` 两个接口落真数据**

登录三步：按 email 查用户 → 校验密码 → 签发 token。
**注意**：邮箱不存在和密码错要返回同一句"邮箱或密码错误"，别提示"用户不存在"（那等于告诉别人哪些邮箱注册过）。

`/auth/me` 直接从 `CurrentUserDep` 拿用户返回 id/name/email。

**4. 补测试 `tests/test_auth.py`**：

| 用例 | 断言 |
|---|---|
| 正确账号密码 | 200，`data.access_token` 非空 |
| 密码错 | 401，`code == "unauthorized"` |
| 不带 token 访问 `/auth/me` | 401 |
| 带 token 访问 `/auth/me` | 200，email 正确 |

> 测试里需要准备数据：可以在 fixture 里直接往测试库插用户，或者调用 seed 的 `main()`。第一周建议每个测试用独立事务、结束后回滚，避免互相污染。

#### 前端（约 3 小时）

**列表页完整实现**：
- `el-table` 列：项目名称 / 品牌 / 别名（`el-tag` 循环渲染）/ 问题数 / 创建时间（`dayjs` 格式化）
- 顶部「新建项目」→ `el-dialog` 弹窗表单
- 底部 `el-pagination`，`total` 用 `meta.total`
- 空数据 `el-empty`：「还没有项目，点右上角新建」
- 点行跳 `/projects/{id}`

**新建弹窗**：别名、竞品用 `el-select` 的 `multiple` + `allow-create` + `filterable`，输入后回车变标签。

#### 周三验收

- [ ] `pytest` 全绿，≥12 个用例
- [ ] 错误密码返回 401 且提示"邮箱或密码错误"
- [ ] 正确密码拿到的 token，在 `/docs` 里点 `/auth/me` 能返回用户信息
- [ ] 前端列表页能看到 2 个项目（此时还是 mock，周四切真接口）

---

### 周四：CRUD 落库 + 联调

#### 后端（约 4 小时）

**1. 写 service 层**（endpoint 里不写 SQL）

`app/services/project.py`：

```python
class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_owner(self, owner_id: str, query: PageQuery) -> tuple[list[Project], int]:
        # 1. count 总数
        # 2. select ... offset/limit 取当前页
        # 3. 查每个项目的 prompt_count 并拼上去
        ...

    async def create(self, owner_id: str, payload: ProjectCreate) -> Project: ...
    async def get_or_404(self, project_id: str, owner_id: str) -> Project: ...
```

`prompt_count` 两种做法：
- 简单版（本周用）：拿到项目列表后，用一次 `select(Prompt.project_id, func.count()).group_by(...)` 查计数，内存里拼上去
- 优雅版（以后再说）：`select(Project, func.count(Prompt.id)).outerjoin(...).group_by(Project.id)`

**2. 三个 endpoint 换真实现**，加上 `CurrentUserDep`：

```python
@router.get("", response_model=ApiResponse[PageData[ProjectRead]])
async def list_projects(query: PageQueryDep, user: CurrentUserDep, service: ProjectServiceDep):
    items, total = await service.list_by_owner(user.id, query)
    return success(
        PageData[ProjectRead](
            items=[ProjectRead.model_validate(p, from_attributes=True) for p in items],
            meta=PageMeta.build(query, total),
        )
    )
```

`ProjectServiceDep` 在 `deps.py` 里定义，写法照搬现有的 `ExampleServiceDep`。

**3. 权限边界**：查项目必须带 `owner_id` 条件，别人的项目返回 404（不要返回 403，403 等于承认"这个 ID 存在"）。

**4. CORS 配置**（忘配必然联调失败）

`.env`：

```
CORS_ORIGINS=http://localhost:5173,http://192.168.1.10:5173
```

改完重启后端。英文逗号分隔，不带空格，不写 JSON 数组。

**5. 补测试** `tests/test_projects.py`、`tests/test_prompts.py`：列表 200、详情 404、新建后能查到、未登录 401。

#### 前端（约 4 小时，主要是联调）

**1. 把 mock 换成真接口**：删假数据，改调 `src/api/project.ts` 的函数。

**2. 详情页实现**：
- 头部：项目名 + 品牌
- `el-descriptions`：品牌 / 别名（标签）/ 竞品（标签）/ 创建时间
- `el-tabs`：Tab「概览」（先放"数据采集功能开发中"空状态）、Tab「问题库」（`PromptTable`）

**3. `PromptTable` 组件**：表格 + 新增按钮 + 弹窗（文本框 + 分类输入）

**4. 联调顺序**（照做，别跳步）：

1. 后端启动：`uvicorn app.main:app --reload --host 0.0.0.0`
2. 浏览器打开 `http://localhost:8000/docs` 确认能开
3. 前端启动：`pnpm dev`
4. F12 → Network，勾选「Preserve log」
5. 登录，看 `login` 请求：状态 200？响应有 `data.access_token`？
6. 跳转后看 `GET /projects`：请求头有 `Authorization`？状态 200？
7. 401 → 看请求头有没有 Authorization（拦截器没生效 / token 没存上）
8. CORS 报错 → 看 `.env` 的 `CORS_ORIGINS` 是否含 `http://localhost:5173`
9. 一直 pending 后超时 → 后端没起来，或 baseURL 端口写错

#### 周四验收

- [ ] 前端关掉所有假数据，全流程走真接口
- [ ] 新建项目后列表立即出现新记录
- [ ] 新建问题后列表数量 +1
- [ ] 退出登录后访问 `/projects` 被踢回登录页
- [ ] `pytest` 全绿，CI 绿

---

### 周五：收口与演示

#### 后端（约 2 小时）

1. 修联调暴露的问题（优先改实现、不改契约；必须改契约时群里说并当天更新 `/docs`）
2. 补 README 启动说明：

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
cp .env.example .env
python -m alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload
```

3. 删掉 `example` 模块（连同测试、schema、service、deps 里的别名）
4. 写 W2 预告：`runs`/`responses` 表字段 + 三个接口形状 + 采集的异步模型
5. 开 PR 合并 `main`，等 CI 绿

#### 前端（约 2 小时）

1. 三态处理：加载中（`v-loading`）、空数据（`el-empty`）、出错（拦截器弹提示，页面保留上次数据）
2. 提交中禁用按钮
3. 准备 3 分钟演示脚本：

```
1. 打开登录页，故意输错密码 → 提示"邮箱或密码错误"
2. 输对密码 → 进入项目列表，指出 2 个项目、分页、别名标签
3. 新建一个项目，填别名和竞品 → 列表立刻出现
4. 点进详情 → 品牌/别名/竞品，切到问题库 → 5 条问题
5. 新增一条问题 → 列表 +1
6. 刷新页面 → 还在登录态
```

#### 周五验收（缺一样算没过）

- [ ] 7 个接口在 `/docs` 全部可点通，结构与文档一致
- [ ] 3 张表建好，`alembic upgrade head` 可重复执行（连跑两次不报错）
- [ ] seed 可重复执行，不重复插入
- [ ] `pytest` ≥ 12 个用例全绿
- [ ] 前端 4 个页面可用，全流程无报错
- [ ] README 能从零跑起来（找个干净环境试一次）
- [ ] W2 接口预告已发出

---

## 八、联调手册（卡住时按这个查）

### 8.1 定位顺序

```
页面没反应 → F12 Console 有报错吗？
              ↓ 有
         是 JS 报错还是接口报错？
              ↓ 接口
         Network 里那个请求状态码多少？
              ↓
    4xx → 请求本身有问题（参数、token）
    5xx → 后端崩了，去看后端终端报错栈
    没有请求 → 前端代码没执行到（看 console）
    CORS → 后端 CORS_ORIGINS 没配前端地址
```

### 8.2 自测命令

```bash
# 健康检查
curl http://127.0.0.1:8000/api/v1/health

# 登录拿 token（Git Bash）
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@geo.local","password":"admin123"}'

# 带 token 请求
curl -s http://127.0.0.1:8000/api/v1/auth/me -H "Authorization: Bearer <粘贴token>"
```

PowerShell（cmd 不支持单引号）：

```powershell
$body = '{"email":"admin@geo.local","password":"admin123"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/auth/login -Method Post -Body $body -ContentType 'application/json'
```

### 8.3 甩锅之前，先各自自查

| 后端自查 | 前端自查 |
|---|---|
| uvicorn 终端有没有报错栈 | Network 里请求 URL 对不对（端口、路径前缀） |
| `/docs` 里同一接口能通吗 | 请求头有没有 `Authorization` |
| `.env` 的 `CORS_ORIGINS` 对不对 | 响应拦截器有没有把 401 吞掉 |
| 数据库里真有数据吗（sqlite3 查） | localStorage 里的 token 是不是字符串 `"undefined"` |

---

## 九、本周的十件"不要做"

1. 不要为了让演示好看而写死数据（看着能用，其实没通）
2. 不要在 endpoint 里直接写 SQL（先写 service，W3 指标计算会大量复用）
3. 不要手改数据库表结构（一律走 Alembic）
4. 不要自己拼错误响应（用框架的 `NotFoundError` / `UnauthorizedError`）
5. 不要把 token 存 `sessionStorage` 又到处读（统一 localStorage + 拦截器）
6. 不要在前端组件里写 `if (res.code === 'ok')`（拦截器已解包）
7. 不要在接口里返回中文硬编码、前端又自己拼一套（错误文案只由后端给）
8. 不要两个人同时改 `router.py`（合并冲突高发区，改前说一声）
9. 不要把 `.env` 提交上去
10. 不要跳过周五演示（不演示就等于不知道有没有真的通）
