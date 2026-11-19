# geo-backend

GEO 优化监测平台后端 —— 监测品牌在生成式引擎（DeepSeek / 豆包 / 通义等）回答中的曝光情况。
基于 FastAPI 基础框架搭建：分层分区、统一响应、统一异常、带链路追踪的日志与 CI 都已就位，
新增业务模块时照 `app/api/v1/endpoints/example.py` 的写法填空即可。

- 技术栈：Python 3.11+ / FastAPI / Pydantic v2 / SQLAlchemy(async) / Postgres / Uvicorn
- 核心链路：问题库 × 引擎 → 采集原始回答 → 提及/位次分析 → 指标看板 → 导出
- 已包含：多环境配置、统一响应信封、全局异常、JSON 日志 + request_id、GitHub Actions CI

## 文档

| 文档 | 内容 |
|---|---|
| [docs/00-basics.md](docs/00-basics.md) | 基础概念：CI、JWT、OpenAPI、数据库迁移、反向代理、systemd |
| [docs/01-week1-plan.md](docs/01-week1-plan.md) | 第一周详细计划：接口契约、表结构、逐日任务、代码骨架、验收标准 |
| [docs/02-deploy.md](docs/02-deploy.md) | 服务器部署手册：Nginx + systemd + Postgres + HTTPS + 更新回滚 |
| [docs/03-github.md](docs/03-github.md) | GitHub 操作清单：建仓、权限、CI、部署密钥、协作规范 |

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
│  ├─ main.py                    # 应用入口：组装中间件 / 异常处理 / 路由
│  ├─ core/                      # 与业务无关的横切能力
│  │  ├─ config.py               # 配置（环境变量 / .env，pydantic-settings）
│  │  ├─ logging.py              # 日志初始化（文本 / JSON 两种格式）
│  │  ├─ context.py              # request_id 上下文（ContextVar）
│  │  ├─ middleware.py           # 请求 ID、耗时统计、访问日志
│  │  ├─ exceptions.py           # 业务异常基类 + 全局异常处理器
│  │  └─ response.py             # 统一响应体 ApiResponse / success / failure
│  ├─ api/
│  │  ├─ deps.py                 # 依赖注入出口（service、分页、鉴权预留）
│  │  └─ v1/
│  │     ├─ router.py            # 路由聚合：新模块在这里挂一行
│  │     └─ endpoints/           # 一个业务域一个文件
│  │        ├─ health.py         # /health、/ready
│  │        └─ example.py        # 示例 CRUD（可整块删除）
│  ├─ schemas/                   # Pydantic 出入参（接口契约）
│  │  ├─ common.py               # 分页等通用模型
│  │  └─ example.py
│  ├─ services/                  # 业务逻辑：编排用例，不 import fastapi
│  ├─ repositories/              # 数据访问层：SQL / 外部存储全部收在这里
│  │  └─ base.py                 # 仓储接口骨架
│  ├─ models/                    # ORM 模型（接库后填）
│  └─ utils/                     # 纯工具函数（时间、加密、ID 生成）
├─ tests/                        # pytest 用例
├─ docs/                         # 计划、部署、协作文档
│  ├─ 00-basics.md               # 基础概念（CI / JWT / 迁移 / 反代）
│  ├─ 01-week1-plan.md           # 第一周计划与接口契约
│  ├─ 02-deploy.md               # 服务器部署手册
│  └─ 03-github.md               # GitHub 操作清单
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
