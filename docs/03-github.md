# GitHub 操作清单

> 一次性的仓库初始化 + 日常协作规范。带 ✅ 的是**现在就做**的，带 ⏳ 的是 W4 上线前做。
> 服务器密钥与部署细节见 [02-deploy.md](./02-deploy.md)；CI 是什么见 [00-basics.md](./00-basics.md)。
> **这份文档假设你从没配过 GitHub 仓库，所有点击位置都写到菜单名。**

---

## 一、仓库规划

| 仓库 | 归属 | 内容 | 可见性 | 谁有写权限 |
|---|---|---|---|---|
| `geo-backend` | 后端（你） | 本仓库（FastAPI + docs） | Private | 后端 |
| `geo-frontend` | 前端 | Vue 3 前端 | Private | 前端 |

两个独立私仓，接口契约靠后端 `/docs` 与 `openapi.json` 传递。不设 submodule、不做 monorepo。

> 想长期省事：建一个 Organization（如 `geo-team`），两个仓都放进去，权限按 Team 分配。两人项目直接加 collaborator 也完全够用，先别折腾组织。

---

## 二、✅ 第一步：把后端仓推上 GitHub

### 2.1 在网页建空仓库

1. 打开 <https://github.com/new>
2. **Repository name** 填 `geo-backend`
3. **Description** 填 `GEO 优化监测平台后端（FastAPI + Postgres）`
4. 选 **Private**
5. **下面三个勾全部不要勾**（Add a README / .gitignore / license）
   → 勾了会在远程生成提交，你本地推的时候会报 `Updates were rejected`，得先 pull 合并，新手最容易卡在这
6. 点 **Create repository**

创建完页面会显示一堆命令，**不要照抄**，用下面的。

### 2.2 本地关联并推送

```bash
cd "C:/Users/李嘉涵/WorkBuddy/spaces/2026_11_17_15-25-50"

# 1) 先确认 SSH 能认到你（回显 Hi <用户名>! 才继续）
ssh -T git@github.com

# 2) 关联远程仓库（注意是 SSH 地址，不是 https）
git remote add origin git@github.com:<你的用户名>/geo-backend.git

# 3) 确认只有一条 origin
git remote -v

# 4) 推送
git push -u origin main
```

**为什么必须用 SSH 地址**：这台机器到 `github.com:443` 直连超时、经内置代理 502，只有 SSH 22 端口通。用 `https://github.com/...` 会卡在 `Writing objects` 然后超时。

如果第 1 步就报 `Permission denied (publickey)`：本机的 `~/.ssh/github_backend_framework.pub` 还没加到账号 → GitHub → 右上角头像 → **Settings** → 左侧 **SSH and GPG keys** → **New SSH key** → Title 随意，Key 粘贴整行 → **Add SSH key**。

### 2.3 仓库基础设置

进入仓库页面，逐个点：

| 位置 | 操作 | 目的 |
|---|---|---|
| 右侧 About 齿轮 → Description | 填一句话介绍 | 别人一眼知道是什么 |
| 同处 → Topics | `fastapi` `python` `geo` `ai-search` `seo` | 便于检索 |
| Settings → General → Features | 取消勾选 **Wikis**；**Projects** 按需（组织级看板更集中） | 避免空壳功能分散注意力 |
| Settings → General → Danger Zone | 默认分支确认是 `main` | 我们本地已是 main |

---

## 三、✅ 第二步：双人权限

### 3.1 前端仓库

1. 前端在自己账号建 `geo-frontend`（同样 Private、不勾 README）
2. 前端仓库 → **Settings** → 左侧 **Collaborators** → **Add people** → 输入你的 GitHub 用户名 → 选 **Write** → 发送邀请
3. 你去邮箱点接受

### 3.2 你的后端仓库

前端不需要写权限，只需要能看接口文档：

1. 后端仓 → **Settings** → **Collaborators** → **Add people** → 前端用户名 → 选 **Read**
2. 或者干脆不给：把 `/docs` 截图/导出给他也行。但联调时他能直接看 `http://<你的IP>:8000/docs`，不用给仓库权限

### 3.3 为什么要分 Write / Read

- **Write**：能推代码、开 PR，但不能删仓库、不能改设置
- **Read**：只能看、只能 clone，不能推
- **Admin**：能改一切，包括删仓库——只留给自己

---

## 四、✅ 第三步：分支保护（防止手滑推坏 main）

进入后端仓 → **Settings** → 左侧 **Branches** → **Add branch protection rule**：

**Branch name pattern** 填 `main`，然后勾：

| 选项 | 勾选 | 作用 |
|---|---|---|
| Require a pull request before merging | ✅ | 谁都不能直接推 main，必须走 PR |
| ↳ Required approvals | `1` | 至少一个人看过 |
| Require status checks to pass before merging | ✅ | CI 不绿不许合并 |
| ↳ 搜索框里选 `check` | ✅ | 就是我们 ci.yml 里的 job 名 |
| Require linear history | ✅ | 禁止 merge commit，历史一条直线好读 |
| Do not allow bypassing the above settings | ✅ | 管理员也不能绕过（自己别给自己开后门） |
| Allow force pushes | ❌ 不勾 | 防止 `push -f` 覆盖历史 |
| Allow deletions | ❌ 不勾 | 防止误删分支 |

点 **Create** 保存。前端仓库同样操作一遍。

> 小团队常见顾虑："就两个人还要 review 太慢"。实际体验：开 PR 自己看一遍 diff，能抓到 80% 的低级错误（调试代码没删、`.env` 混进去、console.log 忘了删）。

---

## 五、✅ 第四步：本机 SSH（只做一次）

```bash
ls ~/.ssh/*.pub                      # 看有没有现成的公钥
ssh-keygen -t ed25519 -C "你的邮箱" -f ~/.ssh/id_ed25519   # 没有就生成，一路回车
cat ~/.ssh/id_ed25519.pub            # 复制输出
```

粘贴位置：GitHub → 头像 → **Settings** → **SSH and GPG keys** → **New SSH key** → **Key type** 选 `Authentication Key` → 粘贴 → **Add SSH key**。

验证：

```bash
ssh -T git@github.com
# 成功：Hi <你的用户名>! You've successfully authenticated, but GitHub does not provide shell access.
```

- 回显是 `Hi <用户名>!` → 账户级密钥，所有仓库通用 ✅
- 回显是 `Hi <owner>/<repo>!` → 这是某个仓库的 Deploy Key，只能操作那一个仓库，其他仓库推不上去
- `Permission denied` → 公钥没加成功，或加到了别的账号

---

## 六、✅ 第五步：CI（后端已有，前端新建）

### 6.1 后端

`.github/workflows/ci.yml` 已经就位，不用改。push 或开 PR 就会自动跑：装依赖 → `ruff check` → `ruff format --check` → `pytest`（Python 3.11/3.12/3.13 各跑一遍）。

看结果：仓库页面顶部 **Actions** 标签 → 点最新一条 → 左边 `check` → 展开看每一步的日志。

### 6.2 前端

在前端仓库新建 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm run lint --if-present
      - run: pnpm run build
```

`--frozen-lockfile` 的作用：`package.json` 改了但 `pnpm-lock.yaml` 没更新时直接失败，防止"我这能装、你那装不上"。改依赖后记得本地 `pnpm install` 把 lock 文件一起提交。

### 6.3 第一次 CI 就红的三种典型原因

| 报错关键字 | 原因 | 修法 |
|---|---|---|
| `No module named 'xxx'` | 新装的包没写进 `pyproject.toml` | 加到 `dependencies`，本地 `pip install -e ".[dev]"` 验证 |
| `Would reformat: xxx.py` | 格式不合规 | 本地 `ruff format .` 后提交 |
| `failed on setup`（依赖 lock） | 前端 lock 文件没提交 | `git add pnpm-lock.yaml` 再推 |

---

## 七、✅ 第六步：Issue 与看板（10 分钟）

### 7.1 标签

仓库 → **Issues** → 右侧 **Labels** → 逐个新建：

| 标签名 | 颜色 | 用途 |
|---|---|---|
| `P0` | 红 `#d73a4a` | 本周必须完成，不做完不推进 |
| `P1` | 黄 `#fbca04` | 下周候选 |
| `backend` | 蓝 `#1d76db` | 后端任务 |
| `frontend` | 绿 `#0e8a16` | 前端任务 |
| `blocked` | 灰 `#6a737d` | 卡住了，需要对方支持 |
| `bug` / `feature` / `docs` | 默认自带 | 类型 |

### 7.2 看板

仓库 → **Projects** → **New project** → 选 **Board** → 命名 `GEO MVP` → 建五列：

```
待办 → 本周 → 进行中 → 待联调 → 已完成
```

### 7.3 把 W1 的任务建进去（照抄）

后端（自己认领）：

- [ ] `POST /auth/login` + `GET /auth/me`
- [ ] `GET/POST /projects` + `GET /projects/{id}`
- [ ] `GET/POST /projects/{id}/prompts`
- [ ] 3 张表 + Alembic 迁移 + seed 脚本
- [ ] JWT 鉴权与当前用户依赖
- [ ] pytest ≥ 12 个用例

前端（对方认领）：

- [ ] 脚手架 + 路由 + axios 封装
- [ ] 登录页
- [ ] 项目列表页（表格 / 分页 / 新建弹窗）
- [ ] 项目详情页 + 问题库 Tab

两条 `P0` 的卡点任务单独建：

- [ ] 【联调】前端切真接口，全流程走通
- [ ] 【演示】周五 10 分钟演示

---

## 八、✅ 第七步：加上 PR 模板（防接口偷偷改）

在后端仓库新建 `.github/pull_request_template.md`：

```markdown
## 改了什么

<!-- 一句话说清楚 -->

## 怎么验证

<!-- 跑了什么命令、点了哪些页面 -->

## 是否影响接口契约

- [ ] 否
- [ ] 是（已同步给前端，`/docs` 已更新）
```

前端仓库也放一份。

> 这套模板的核心只有第三个问题。接口契约一改，前端所有相关页面都要跟着改，是整个协作里唯一的"高杠杆变更"。**改之前先说，永远比改完再解释便宜。**

---

## 九、⏳ 第八步：服务器部署密钥（W4 前）

服务器要拉私仓，但**绝不能**把你的个人私钥放到服务器上。每个仓库配一把只读的 Deploy Key：

```bash
# 在服务器上执行
ssh-keygen -t ed25519 -C "server@geo" -f ~/.ssh/geo_backend -N ""
cat ~/.ssh/geo_backend.pub
```

GitHub 网页：后端仓 → **Settings** → 左侧 **Deploy keys** → **Add deploy key** → Title 填 `geo-server`，Key 粘贴 → **Allow write access 不勾** → **Add key**。

前端仓同样来一把（`geo_frontend`）。

服务器上配 `~/.ssh/config` 指定用哪把钥匙，完整步骤见 [02-deploy.md](./02-deploy.md) 第五节。

> 为什么用 Deploy Key 而不是账号密钥：Deploy Key 只对一个仓库有效、默认只读，泄露了损失可控；账号密钥泄露等于整个 GitHub 账号被人接管。

---

## 十、⏳ 第九步：自动部署（W4 前）

### 10.1 配 Secrets

后端仓 → **Settings** → 左侧 **Secrets and variables** → **Actions** → **New repository secret**，逐个添加：

| Name | Value | 说明 |
|---|---|---|
| `SERVER_HOST` | 服务器 IP 或域名 | 不要带 `http://` |
| `SERVER_USER` | `deploy` | 不要用 root |
| `SERVER_PORT` | `22` | 改过 SSH 端口的填实际值 |
| `SERVER_SSH_KEY` | 服务器 `~/.ssh/id_ed25519` 的私钥全文 | 含首尾 `-----BEGIN...` 行，别删换行 |

建议再建一个 Environment：**Settings** → **Environments** → **New environment** → 命名 `production` → 勾 **Required reviewers** 选自己 → 把上面四个 Secret 移到环境级。这样每次部署都要你点一次确认，防止误触发。

### 10.2 部署工作流

后端仓新建 `.github/workflows/deploy.yml`：

```yaml
name: Deploy

on:
  workflow_dispatch:        # 手动点按钮触发（推荐先这样用）
  push:
    tags: ["v*"]            # 打 tag 时自动发布

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - name: SSH 到服务器执行 deploy.sh
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          port: ${{ secrets.SERVER_PORT }}
          script: /opt/geo/deploy.sh
```

这条工作流本身只做一件事：登服务器、跑 `deploy.sh`。所有部署逻辑都在服务器的脚本里，出问题直接上服务器看 `journalctl -u geo-api`，比在 CI 日志里翻容易得多。

### 10.3 怎么触发

- 想立刻发版：仓库 → **Actions** → 左侧 **Deploy** → 右侧 **Run workflow** → 选 `main` → **Run**
- 想按版本发：`git tag -a v0.1.0 -m "MVP" && git push origin v0.1.0`

---

## 十一、日常协作规范

### 11.1 分支

| 分支 | 用途 | 谁能推 |
|---|---|---|
| `main` | 永远可部署 | 谁都不能（只能通过 PR 合并） |
| `feat/xxx` | 新功能，如 `feat/project-crud` | 对应的 owner |
| `fix/xxx` | 修 bug，如 `fix/login-401` | 对应的 owner |
| `chore/xxx` | 配置、依赖、文档 | 谁都可以 |

命名要点：小写、连字符、能看出做什么。`feat/project-crud` 比 `dev1` 好一百倍——三个月后你还看得懂。

### 11.2 提交信息

格式：`<type>(<scope>): <中文简述>`

```
feat(projects): 新增项目分页列表接口
fix(auth): 修复 token 过期未返回 401
docs(deploy): 补充 systemd 配置说明
chore(deps): 升级 sqlalchemy 到 2.0.36
refactor(prompts): 把查询逻辑从 endpoint 移到 service
test(auth): 补充密码错误的用例
```

`type` 只有这几种：`feat` / `fix` / `docs` / `chore` / `refactor` / `test`。

为什么要规范：以后查"这个字段什么时候改的"时，`git log --grep="feat(projects)"` 一秒定位。

### 11.3 一个功能的完整流程

```bash
# 1. 从最新 main 切分支
git switch main
git pull
git switch -c feat/project-crud

# 2. 写代码，中途可以多次提交
git add -A
git commit -m "feat(projects): 新增项目分页列表接口"

# 3. 推送
git push -u origin feat/project-crud

# 4. 网页上会弹出"Compare & pull request"，点它
#    base: main  ←  compare: feat/project-crud
#    标题写清楚，正文填模板
#    右侧 Reviewers 选对方
#    点 Create pull request

# 5. 等 CI 绿 → 对方点 Approve → 点 Squash and merge
# 6. 合并完点 Delete branch，本地也删掉
git switch main && git pull && git branch -d feat/project-crud
```

### 11.4 合并方式为什么用 Squash

| 方式 | 结果 |
|---|---|
| Merge commit | 每合并一次多一个"Merge branch..."节点，历史变麻花 |
| Rebase and merge | 历史干净，但每个人的每个小提交都留在 main 上 |
| **Squash and merge** | 一个 PR 压成一个提交，main 上每个点都是"一个完整功能" ✅ |

### 11.5 冲突怎么办

```bash
git switch feat/xxx
git pull --rebase origin main     # 把 main 的新改动先合进来
# 打开冲突文件，找 <<<<<<< ======= >>>>>>> 三段，留想要的，删标记
git add <冲突文件>
git rebase --continue
git push --force-with-lease       # 注意用 with-lease，不要裸 --force
```

两个人项目冲突最多的三个文件：`router.py`（都在加路由）、`pyproject.toml`（都在加依赖）、`pnpm-lock.yaml`（前端必冲突，改完重新 `pnpm install` 就好）。改这三个之前先在群里说一声，能省一半冲突。

---

## 十二、发版

W4 演示通过后：

```bash
git switch main && git pull
git tag -a v0.1.0 -m "MVP：登录 / 项目 / 问题库 / 采集 / 看板"
git push origin v0.1.0
```

网页：**Releases** → **Draft a new release** → 选 `v0.1.0` → 写三条"这版能做什么" → **Publish release**。

Release 的意义：半年后回头看，知道 v0.1.0 那天的系统长什么样、有哪些能力。

---

## 十三、每次发版前 5 分钟检查

- [ ] `main` 上 CI 是绿的（Actions 页面最新一条是绿勾）
- [ ] 没有残留的 `feat/*` 分支（该合的都合了，该删的都删了）
- [ ] 后端 `.env.example` 里新增的变量都写了说明（别人照着才能跑起来）
- [ ] 前端生产构建的 `VITE_API_BASE` 是 `/api/v1`（相对路径）
- [ ] 服务器有当天的数据库备份（`ls /opt/geo/backups`）
- [ ] 打了 tag 并写了 Release 说明

---

## 十四、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `Permission denied (publickey)` | 密钥没加 / 加成了单仓 Deploy Key | `ssh -T git@github.com` 看回显 |
| `push` 卡在 Writing objects 然后超时 | 用了 https 地址 | `git remote set-url origin git@github.com:<你>/<仓>.git` |
| `Updates were rejected` | 远程有你本地没有的提交 | `git pull --rebase origin main` 再推 |
| `fatal: remote origin already exists` | 重复 `remote add` | `git remote set-url origin <新地址>` |
| 误提交了 `.env` 或密钥 | — | **先换密钥/密码**，再 `git rm --cached .env` 提交；历史里的残留需 `git filter-repo` 清理 |
| Actions 不触发 | yml 缩进错 / `on:` 里写了 `master` | 我们是 `main`；用 <https://www.yamllint.com> 检查缩进 |
| Actions 额度 | 私仓每月 2000 分钟 | 两人项目远够；超了先查 workflow 触发条件是否太宽 |
| 提交在 GitHub 不显示为自己的 | `user.email` 不是账号已验证邮箱 | `git config --global user.email "你的GitHub邮箱"` |
| 大文件推不上去（>100MB） | GitHub 单文件上限 100MB | 别提交数据库/日志/构建产物，`.gitignore` 加规则 |

---

## 十五、给两个人的最小纪律（贴在群里）

1. **不直接推 main**，一律走 PR，CI 绿了再合
2. **改接口先说**，说完整理进 PR 模板第三个勾选框
3. **每天一条进展**（三行：昨天做了 / 今天做 / 卡在哪）
4. **卡住超过 30 分钟就喊人**，别一个人硬啃一下午
5. **提交信息写清楚做了什么**，别写 `update`、`fix bug`
