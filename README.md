# geo-backend

GEO 优化监测平台后端 —— 监测品牌在生成式引擎（DeepSeek / 豆包 / 通义等）回答中的曝光情况。

技术方案是 **Supabase 直连（BaaS）**，前端直接读视图，所以这里没有 REST 业务接口。本仓库负责三件事：
**去各家引擎问问题 → 判断回答里提到了谁 → 算成每天的指标**，以及一套留着做运维与扩展的 FastAPI 骨架。

- 技术栈：Python 3.11+ / SQLAlchemy 2（async）/ asyncpg / Supabase Postgres / httpx
- 核心链路：问题库 × 引擎 → 采集原始回答 → 提及/位次分析 → 每日指标 → 看板视图 → 导出
- 保留能力：多环境配置、统一响应信封、全局异常、JSON 日志 + request_id、GitHub Actions CI

## 文档

| 文档 | 内容 |
|---|---|
| [docs/00-basics.md](docs/00-basics.md) | 基础概念：CI、JWT、OpenAPI、数据库迁移、反向代理、systemd |
| [docs/04-supabase-contract.md](docs/04-supabase-contract.md) | 数据契约：6 张表 + 4 个视图 + 采集器契约（当前方案） |
| [docs/05-auth-permission.md](docs/05-auth-permission.md) | 认证与权限：角色矩阵、RLS、触发器、前端登录代码 |
| [docs/06-frontend-handoff.md](docs/06-frontend-handoff.md) | 发给前端的对接清单（SQL 执行步骤、控制台设置、排错） |
| [docs/07-backend.md](docs/07-backend.md) | 采集与分析服务：结构、命令行、指标口径、加引擎、定时任务 |
| [docs/02-deploy.md](docs/02-deploy.md) | 服务器部署手册：Nginx + systemd + HTTPS + 更新回滚 |
| [docs/03-github.md](docs/03-github.md) | GitHub 操作清单：建仓、权限、CI、部署密钥、协作规范 |

## 采集服务快速开始

```bash
pip install -e ".[dev]"

# 本地演示：SQLite 建表 + 灌 7 天示例数据（不花钱，不需要任何 API key）
python -m app.cli init
python -m app.cli seed --days 7
python -m app.cli check

# 真采集（.env 里配好 DEEPSEEK_API_KEY / QWEN_API_KEY / DOUBAO_API_KEY）
python -m app.cli collect --project 演示项目 --dry-run   # 只试跑不写库
python -m app.cli collect --project 演示项目 --metrics   # 采集并算当天指标
```

线上把 `DATABASE_URL` 指向 Supabase，表由 `docs/sql/01-schema.sql` 建，不用 `cli init`。

## 框架快速开始（保留的 FastAPI 骨架）

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. 安装依赖（含开发依赖 ruff / pytest / httpx）
pip install -e ".[dev]"

# 3. 准备环境变量
cp .env.example .env      # Windows 用 copy .env.example .env

# 4. 启动
uvicorn app.main:app --reload
# 或者：python -m app.main
```

- 接口地址：<http://127.0.0.1:8000/api/v1/health>
- 交互文档：<http://127.0.0.1:8000/docs>（`ENV=prod` 时自动关闭）

## 目录结构

```
.
├─ app/
│  ├─ cli.py                     # 命令行入口：init / check / seed / collect / metrics / reanalyze
│  ├─ main.py                    # FastAPI 入口（健康检查等运维接口保留）
│  ├─ core/                      # 与业务无关的横切能力
│  │  ├─ config.py               # 配置（环境变量 / .env，pydantic-settings）
│  │  ├─ db.py                   # 异步 engine + session 工厂
│  │  ├─ logging.py              # 日志初始化（文本 / JSON 两种格式）
│  │  ├─ context.py              # request_id 上下文（ContextVar）
│  │  ├─ middleware.py           # 请求 ID、耗时统计、访问日志
│  │  ├─ exceptions.py           # 业务异常基类 + 全局异常处理器
│  │  └─ response.py             # 统一响应体 ApiResponse / success / failure
│  ├─ engines/                   # 引擎适配层（加一家引擎只改这里）
│  │  ├─ base.py                 # EngineClient 协议 + 统一结果 EngineAnswer
│  │  ├─ openai_compat.py        # OpenAI 兼容实现（DeepSeek / 通义 / 豆包共用）
│  │  └─ registry.py             # 引擎注册表：密钥、模型、计费单价
│  ├─ services/                  # 业务逻辑：编排用例，不 import fastapi
│  │  ├─ collector.py            # run_once：并发采集 → 落库 → 提及分析
│  │  ├─ analyzer.py             # 品牌命中、位次、情绪、引用域名（纯函数）
│  │  ├─ metrics.py              # 聚合 daily_metrics
│  │  └─ seed.py                 # 演示数据
│  ├─ models/                    # ORM 模型
│  │  ├─ base.py                 # 声明式基类 + JSON 类型
│  │  └─ geo.py                  # 6 张业务表
│  ├─ api/v1/endpoints/          # 运维接口（health / ready / 示例）
│  ├─ schemas/                   # Pydantic 出入参
│  ├─ repositories/              # 数据访问骨架（当前业务走 services 里的 SQLAlchemy 查询）
│  └─ utils/                     # 纯工具函数
├─ tests/                        # pytest 用例（26 个）
├─ docs/                         # 计划、契约、部署、协作文档（见上方表格）
├─ docs/sql/                     # 可直接在 Supabase 执行的 SQL
│  ├─ 00-lock-write.sql          # 应急：关掉演示表的匿名写权限
│  ├─ 01-schema.sql              # 6 张业务表 + 4 个视图
│  └─ 02-auth.sql                # 认证与权限（RLS / 触发器 / RPC）
├─ .github/workflows/ci.yml      # 提交即跑 lint + format + test
├─ .env.example                  # 环境变量模板（.env 不入库）
├─ .gitignore
└─ pyproject.toml                # 依赖、ruff、pytest 配置
```

## 分层约定

调用方向单向向下，禁止反向依赖：

```
endpoints（HTTP 解析、组装响应）
    ↓
services（业务规则、事务边界）
    ↓
repositories（数据读写）
    ↓
models（表结构）
```

- `endpoints` 不写业务判断，只做参数解析 + 调 service + 包响应。
- `services` 不 import `fastapi`，保证业务逻辑可被脚本、定时任务、消息消费者复用。
- `repositories` 之外不出现 SQL / 驱动调用。
- 跨层共享的只有 `schemas`（契约）与 `core`（基础设施）。

## 统一响应体

所有接口（含错误）都返回同一个信封，前端只需判断 `code` 一处：

```json
{
  "code": "ok",
  "message": "success",
  "data": { "status": "ok" },
  "request_id": "6f1c2a9d8b7e4a10"
}
```

- `code=ok` 表示成功；业务错误码如 `not_found`、`validation_error`、`internal_error`。
- HTTP 状态码仍然表达传输层语义（401 / 404 / 422 / 500 等），便于网关与监控识别。
- `request_id` 同时写回响应头 `X-Request-ID`，用户反馈问题时带上它即可定位全链路日志。

## 统一异常

业务代码只抛异常，不自己拼 HTTP 响应：

```python
from app.core.exceptions import NotFoundError, ForbiddenError

raise NotFoundError("订单不存在", data={"order_id": order_id})
```

内置：`BadRequestError`(400)、`UnauthorizedError`(401)、`ForbiddenError`(403)、
`NotFoundError`(404)、`ConflictError`(409)、`TooManyRequestsError`(429)、`ServiceUnavailableError`(503)。

参数校验失败（422）与未捕获异常（500）已统一转成同样的信封；`ENV!=prod` 时 500 会带出异常类型和消息，生产环境只返回“服务内部错误”。

## 日志

- 每个请求自动分配 `request_id`（客户端传 `X-Request-ID` 则沿用），通过 `ContextVar` 注入到所有日志行，无需逐层传参。
- `LOG_JSON=true` 输出单行 JSON，直接被 ELK / Loki / 云日志采集；`false` 输出人类可读文本，方便本地调试。
- 访问日志包含 `method / path / status_code / duration_ms / client_ip`。

## 配置

全部通过环境变量或 `.env` 注入，代码里不出现硬编码地址与密钥。字段见 `app/core/config.py` 与 `.env.example`。

`ENV` 取值：`local` / `dev` / `staging` / `prod`。`prod` 下自动关闭 `/docs`、`/redoc`、`/openapi.json`。

## 新增一个业务模块（示例：订单）

1. `app/schemas/order.py`：定义 `OrderCreate` / `OrderRead`。
2. `app/services/order.py`：写 `OrderService` 与 `get_order_service()`。
3. `app/api/deps.py`：加 `OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]`。
4. `app/api/v1/endpoints/order.py`：照 `example.py` 写路由，返回 `success(...)`。
5. `app/api/v1/router.py`：`api_router.include_router(order.router)`。
6. `tests/test_order.py`：至少覆盖主流程 + 一个异常分支。

## 接入数据库时

1. `app/core/db.py`：建异步 engine、`async_session_maker`、`get_session()` 依赖。
2. `app/models/*.py`：定义 ORM 模型，共用 `Base`。
3. `app/repositories/*.py`：继承 `app/repositories/base.py` 的 `BaseRepository`。
4. service 里通过构造函数注入仓储（替换掉 `ExampleService` 的内存字典）。
5. 用 Alembic 管理迁移，脚本放 `migrations/`，CI 里加一步 `alembic upgrade head --sql` 做校验。
6. `app/api/v1/endpoints/health.py` 的 `/ready` 加上数据库连通性检查。

## 质量与 CI

```bash
ruff check .            # 静态检查
ruff format .           # 格式化
pytest                  # 测试
```

`.github/workflows/ci.yml` 在 push / PR 时对 Python 3.11、3.12、3.13 分别跑 lint、格式检查、测试。

## 提交规范

- 分支：`main` 保持可发布，功能分支 `feat/xxx`、修复分支 `fix/xxx`。
- 提交信息建议 `type(scope): subject`，如 `feat(order): 新增订单创建接口`。
- `.env`、密钥、`logs/`、`.venv/` 已在 `.gitignore` 中排除，不要提交。
