# 基础概念（开工前先看懂这五个词）

> 这份文档不涉及具体代码，只解释第一周会反复听到的五个概念。
> 看不懂某个词时回来查，别在群里硬猜。

## 一、CI 到底是什么

### 一句话

CI（Continuous Integration，持续集成）= **每次有人提交代码，机器自动帮你把"这代码能不能跑"验一遍**。

### 没有 CI 时会发生什么

真实场景，两个人都写了三天：

1. 后端改了 `Project` 的字段名，从 `brand` 改成 `brand_name`
2. 前端还在用 `brand`，本地跑得好好的，因为他连的是自己的 mock
3. 周四联调，前端页面一片空白
4. 两个人花了两个小时互相怀疑："你那边是不是没部署？""你是不是缓存没清？"
5. 最后发现是一个字段名

CI 不能阻止第 1 步的改动，但能保证：**任何一次提交，只要让"代码跑不起来"，就会立刻亮红，谁都能看见。**

### CI 具体跑哪些东西

对我们这个项目，CI 每次做四件事（都在 GitHub 的服务器上做，不占你的电脑）：

| 步骤 | 命令 | 检查什么 |
|---|---|---|
| 装依赖 | `pip install -e ".[dev]"` | `pyproject.toml` 里写的依赖能不能装上、有没有写漏 |
| 代码规范 | `ruff check .` | 有没有未使用的 import、未定义变量、明显的坑 |
| 格式 | `ruff format --check .` | 缩进、引号、换行是否统一（两个人各写各的风格会很难读） |
| 单元测试 | `pytest` | 你写的 8 个测试用例是不是全过 |

前端仓库的 CI 做两件事：装依赖、`pnpm build`（能打包成功说明没有语法错误、没有引用不存在的文件）。

**注意：CI 不检查"功能对不对"。** 它只能告诉你代码能跑、测试能过。业务逻辑对不对，还得靠测试用例和人工验收——这也是为什么我们要在 W1 写测试。

### 它在什么时候自动跑

`.github/workflows/ci.yml` 开头这几行决定了触发时机：

```yaml
on:
  push:
    branches: [main, develop]     # 有人往这两个分支推代码
  pull_request:
    branches: [main, develop]     # 有人开 PR 想合并进这两个分支
```

所以实际体验是：你 `git push` 完，打开仓库页面的 **Actions** 标签，能看到一条正在跑的任务，一两分钟后变成绿色对勾（通过）或红色叉（失败）。

### CI 里的几个名词

| 名词 | 含义 | 在我们项目里是什么 |
|---|---|---|
| workflow | 一份自动化说明书 | `.github/workflows/ci.yml` 这个文件 |
| job | 一个独立任务 | 我们只有一个 job，叫 `check` |
| step | job 里的一步 | 「装依赖」「跑 ruff」「跑 pytest」各是一步 |
| runner | 执行任务的机器 | GitHub 免费提供的 Ubuntu 虚拟机，用完就销毁 |
| status check | 状态检查结果 | PR 页面上那个绿勾/红叉 |

### 我们的 ci.yml 逐行解释

```yaml
name: CI                          # 显示在 Actions 页面上的名字

on:                               # 什么时候触发
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

concurrency:                      # 同一分支连续推两次，取消前一次，省额度
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:                          # job 名字，分支保护里要勾的就是这个
    runs-on: ubuntu-latest        # 跑在 GitHub 的 Ubuntu 虚拟机上
    strategy:
      fail-fast: false            # 一个版本挂了不要取消其他版本，方便看全貌
      matrix:
        python-version: ["3.11", "3.12", "3.13"]   # 三个版本各跑一遍

    steps:
      - uses: actions/checkout@v4          # 1. 把仓库代码拉到虚拟机上
      - uses: actions/setup-python@v5      # 2. 装指定版本的 Python
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip                       #    缓存 pip 下载，第二次快很多
      - run: |                             # 3. 装项目依赖
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - run: ruff check .                  # 4. 规范检查
      - run: ruff format --check .         # 5. 格式检查（只检查不修改）
      - run: pytest                        # 6. 跑测试
        env:
          ENV: dev                         #    给测试进程的环境变量
          LOG_JSON: "true"
```

看懂了这份文件，你就看懂了所有 CI 配置——前端仓库那份只是把 Python 换成 Node、把 `pytest` 换成 `pnpm build`，结构一模一样。

### 红了怎么办

1. 点进失败的那条，展开红色步骤，读报错最后 10 行
2. 90% 的情况是：依赖没写进 `pyproject.toml`、有个 import 拼错、测试数据冲突
3. 本地复现：在项目根目录手动跑那条命令（`ruff check .` / `pytest`），报错信息一致
4. 改完再推一次，CI 会重新跑

**不要直接点 Re-run 就完事**——如果不是网络抖动导致的失败，重跑一百次也还是红。

### CI 和 CD 的区别

| | CI | CD（Continuous Deployment） |
|---|---|---|
| 做什么 | 验证代码能跑 | 自动把代码部署到服务器 |
| 我们的配置 | `ci.yml`，每次 push 都跑 | `deploy.yml`，**手动触发或打 tag** 才跑 |
| 什么时候有 | 现在就有 | W4 上线前才配 |

W4 的那个自动部署，本质就是让 GitHub 帮你 SSH 到服务器、执行 `deploy.sh`。它省的是"每次上线手动敲命令"的重复劳动，和 CI 是两件事。

### 免费额度会不会超

GitHub 私仓每月 2000 分钟免费。我们每次跑 3 个 Python 版本 ≈ 3 分钟，就算一天推 10 次也才 30 分钟/天，一个月 900 分钟，够用。
真怕超就把 `matrix` 里砍到只留 `"3.12"` 一个版本。

## 二、JWT 是什么

一句话：**一张服务器签发的、带签名的"电子门票"，客户端自己保存，每次请求出示。**

流程：

```
1. 前端把 邮箱+密码 发给后端
2. 后端校验通过，生成一段字符串（就是 token），返回给前端
3. 前端存到 localStorage，之后每个请求都带上：
   Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
4. 后端收到请求，用 JWT_SECRET 验证签名
   - 签名对、没过期 → 认出是谁
   - 签名错 / 过期 → 返回 401
```

三个要点：

- **token 里有内容但不加密**：任何人都能 base64 解码看到里面的 `sub`（用户 ID）和 `exp`（过期时间）。所以**绝不放密码**。
- **改不了**：改了内容签名就对不上，后端会拒收。这就是"签名"的作用。
- **服务器不存 token**：所以叫"无状态"。好处是重启服务、多台服务器都不影响；坏处是没法主动让某个 token 立刻失效（只能等它过期）。我们设 24 小时，够用。

`JWT_SECRET` 是签名用的密钥，泄露了别人就能自己伪造 token。所以生产环境必须换成随机值，`.env` 不能进仓库。

## 三、OpenAPI / Swagger 是什么

一句话：**后端代码自动生成的接口说明书，还能直接在网页上点着试。**

FastAPI 的做法：你写好函数签名和 Pydantic 模型，它自动生成 `openapi.json`（机器读的 JSON）和 `/docs` 页面（人看的网页）。

对我们的价值：

- 前端不用问"这个接口要传什么字段"，打开 `/docs` 就能看到每个字段的类型、是否必填、示例
- 可以点「Try it out」直接发请求，不用装 Postman
- 字段改了，文档自动跟着变——不会出现"文档写的是 brand，代码早就改成 brandName"这种事故

所以我们的协作约定是：**接口文档不手写，靠 `/docs`。** 谁改接口，谁负责让 `/docs` 显示正确。

## 四、数据库迁移（Alembic）是什么

一句话：**把"改表结构"这件事变成可回放、可提交到 Git 的脚本。**

为什么不能直接手改数据库？因为你和服务器是两个数据库：

- 你本地 `data/geo.db` 加了个 `category` 字段
- 服务器上的 Postgres 不知道这件事
- 部署完，接口一查就报 `column category does not exist`

Alembic 的做法：

```
1. 你改 Python 里的模型（app/models/prompt.py 加一行 category）
2. 运行 alembic revision --autogenerate -m "add category"
   → 自动对比模型和数据库，生成一个迁移脚本 migrations/versions/xxxx_add_category.py
   → 脚本里写着 up 怎么加字段、down 怎么删回去
3. 运行 alembic upgrade head
   → 执行这个脚本，数据库就多了一列
4. 这个脚本提交到 Git，服务器部署时跑同一条 upgrade 命令，就同步了
```

规则：**永远不要手改数据库结构**。所有表结构变更都走"改模型 → 生成迁移 → 提交 → 部署时执行"。

## 五、反向代理（Nginx）是什么

一句话：**用户只访问 Nginx，由它决定请求该交给谁。**

为什么不让用户直接访问 8000 端口？

| 直接访问 8000 | 经过 Nginx |
|---|---|
| 端口要暴露在公网，容易被扫 | 8000 只监听 127.0.0.1，外部进不来 |
| 要自己配 HTTPS 证书 | Nginx 统一管证书、自动续期 |
| 前端静态文件还要另起一个服务 | Nginx 顺手就把静态文件发了，性能更好 |
| 采集接口慢会拖死服务 | 可以配超时、限流、缓存 |
| 日志、压缩、跨域都要自己写 | Nginx 一行配置搞定 |

所以生产架构是：Nginx 对外 443，内部把 `/api/` 转给 `127.0.0.1:8000`，把 `/` 直接返回前端打包好的 `index.html`。

## 六、systemd 是什么

一句话：**Linux 上的"服务管家"，负责让程序开机自启、挂了自动重启。**

手动跑 `uvicorn app.main:app` 的问题：关掉终端就停了、服务器重启就没了、崩了没人管。

写成 systemd 服务（`/etc/systemd/system/geo-api.service`）之后：

- `systemctl start geo-api` 启动
- `systemctl enable geo-api` 开机自启
- 程序崩了 3 秒后自动拉起（`Restart=always`）
- `systemctl status geo-api` 看状态
- `journalctl -u geo-api -f` 实时看日志

我们的后端、以及 W2 之后的采集 worker，都各是一个 systemd 服务。
