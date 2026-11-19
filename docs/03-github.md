# GitHub 操作清单

> 一次性的仓库初始化 + 日常协作规范。带 ✅ 的是**你现在就要做**的，带 ⏳ 的是 W4 上线前做。
> 服务器端的密钥与部署细节见 [02-deploy.md](./02-deploy.md)。

## 一、仓库规划

| 仓库 | 归属 | 内容 | 可见性 |
|---|---|---|---|
| `geo-backend` | 你 | 本仓库（FastAPI + docs） | Private |
| `geo-frontend` | 学弟 | Vue 3 前端 | Private |

两个独立私仓，接口契约靠后端 `/docs` 与 `openapi.json` 传递，不设 submodule、不搞 monorepo。

> 想省事也可以建一个 GitHub Organization（如 `geo-team`）把两个仓放进去，后面加人、配权限更清爽。两人项目直接挂 collaborators 也够用。

## 二、一次性初始化（✅ 现在就做）

### 1. 后端仓推上去并改名

当前本地仓库的 remote 还是空的。按顺序做：

```bash
# 1) 在 GitHub 网页新建 Private 空仓，命名 geo-backend，不要勾 README/gitignore
# 2) 本地关联并推送（走 SSH，本机 HTTPS 到 github.com:443 不通）
cd "C:/Users/李嘉涵/WorkBuddy/spaces/2026_11_17_15-25-50"
git remote add origin git@github.com:<你的用户名>/geo-backend.git
ssh -T git@github.com                      # 先确认回显 Hi <你的用户名>!
git push -u origin main
```

之后本地目录名不用改，仓库在 GitHub 上叫什么就是什么。

### 2. 仓库基础设置

| 位置 | 操作 |
|---|---|
| Settings → General → Description | `GEO 优化监测平台后端（FastAPI + Postgres）` |
| Settings → General → Topics | `fastapi` `python` `geo` `ai-search` `seo` |
| Settings → General → Features | 关掉 Wiki、Projects（用组织级看板），保留 Issues |
| Settings → Collaborators（学弟的前端仓） | 让学弟把你加为 `Write`，方便联调时提 PR |
| 你的后端仓 | 把学弟加为 `Read`（他能读接口文档，不能改代码） |

### 3. 分支保护（main）

Settings → Branches → Add branch protection rule，分支填 `main`：

- ✅ Require a pull request before merging（Required approvals: 1）
- ✅ Require status checks to pass（选 `CI / check`）
- ✅ Require linear history
- ✅ Do not allow force pushes
- ⬜ 小团队不必开 Require review from Code Owners

### 4. 本机 SSH（只做一次）

```bash
ls ~/.ssh/github_backend_framework.pub      # 之前已生成的密钥
ssh -T git@github.com                       # 账户级密钥会回显 Hi <用户名>!
```

- 公钥没加过：GitHub → Settings → SSH and GPG keys → New SSH key → 粘贴
- 回显是 `Hi <owner>/<repo>!` 说明加成了某仓库的 Deploy Key，不是账户密钥——这种情况其他仓库推不上去

> **本机网络限制**：`github.com:443` 直连超时、经内置代理 502，只有 SSH 22 端口通。所以 git 一律用 `git@github.com:...` 地址，不要用 https 地址，否则 `push` 必然失败。

## 三、CI（✅ 后端已有，学弟新建）

### 后端

`.github/workflows/ci.yml` 已就位，push / PR 自动跑 `ruff check` + `ruff format --check` + `pytest`（3.11/3.12/3.13）。无需改动。

### 前端（学弟在 `geo-frontend` 建）

`.github/workflows/ci.yml`：

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

## 四、服务器部署密钥（✅ 与 02 文档同步做）

每仓一把只读密钥，绝不放你的个人私钥：

1. 服务器上 `ssh-keygen -t ed25519 -f ~/.ssh/geo_backend -N ""`（前端再来一把）
2. 复制 `.pub` 内容
3. GitHub → 仓库 → Settings → **Deploy keys** → Add deploy key → 粘贴，**不勾** Allow write access

## 五、自动部署（⏳ W4 上线前做）

### 1. 配置 Secrets

后端仓 → Settings → Secrets and variables → Actions → New repository secret：

| Secret | 值 |
|---|---|
| `SERVER_HOST` | 服务器 IP 或域名 |
| `SERVER_USER` | `deploy` |
| `SERVER_PORT` | `22` |
| `SERVER_SSH_KEY` | 服务器 `~/.ssh/id_ed25519` 的**私钥全文**（含首尾行） |

建议在 Settings → Environments 新建 `production`，把 Secret 放在环境级，顺便加一个 Required reviewers，防止误触发。

### 2. 部署工作流

`.github/workflows/deploy.yml`：

```yaml
name: Deploy
on:
  workflow_dispatch:          # 手动触发，安全
  push:
    tags: ["v*"]              # 打 tag 时自动发布

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - name: Run deploy.sh on server
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          port: ${{ secrets.SERVER_PORT }}
          script: /opt/geo/deploy.sh
```

`deploy.sh` 的内容在 02 文档第十三节。这条工作流只是「登录服务器跑一个脚本」，逻辑都在服务器上，出问题看 `journalctl` 就行。

### 3. 前端自动部署（可选）

前端仓同样加一份 `deploy.yml`，把 `script` 换成前端部分的命令（`git pull && pnpm build && rsync`）。或者干脆都由后端仓这一个工作流统管——两人项目，一个入口更不容易乱。

## 六、日常协作规范

### 分支

| 分支 | 用途 |
|---|---|
| `main` | 永远可部署，受保护，不直接推 |
| `feat/xxx` | 新功能，如 `feat/project-crud` |
| `fix/xxx` | 修 bug |
| `chore/xxx` | 配置、依赖、文档 |

### 提交信息

```
<type>(<scope>): <中文简述>
feat(projects): 新增项目分页列表接口
fix(auth): 修复 token 过期未返回 401
docs(deploy): 补充 systemd 配置
chore(deps): 升级 sqlalchemy 到 2.0.36
```

### PR 流程

1. 从 `main` 切分支 → 开发 → `git push origin feat/xxx`
2. 网页开 PR，base 选 `main`，标题同提交规范
3. 等 CI 绿 → 对方 review → **Squash and merge**
4. 合并后删分支

建议加个 PR 模板 `.github/pull_request_template.md`：

```markdown
## 改了什么
<!-- 一句话 -->

## 怎么验证
<!-- 跑了什么命令 / 点了哪些页面 -->

## 是否影响接口契约
- [ ] 否
- [ ] 是（已同步给前端，openapi 已更新）
```

> 「是否影响接口契约」这一条是这套协作里最值钱的检查项，接口一改前端就白干。

### 发版

W4 演示通过后打第一个 tag：

```bash
git tag -a v0.1.0 -m "MVP: 登录/项目/问题库/采集/看板"
git push origin v0.1.0
```

## 七、看板与任务（✅ 与 W1 同步建）

1. 仓库 → Issues → Labels，保留并新增：

| Label | 颜色建议 | 用途 |
|---|---|---|
| `P0` | 红 | 本周必须完成 |
| `P1` | 黄 | 下周候选 |
| `backend` / `frontend` | 蓝 / 绿 | 归属 |
| `bug` / `feature` / `docs` | 默认 | 类型 |
| `blocked` | 灰 | 卡住了，需要对方支持 |

2. Projects → New project → Board 视图，五列：`待办` / `本周` / `进行中` / `待联调` / `已完成`

3. 把 W1 的 7 个接口和 4 个页面各建一条 Issue，你认领后端、学弟认领前端，互相能看到进度。

## 八、每次发版前 5 分钟检查

- [ ] `main` 上 CI 是绿的
- [ ] 没有未合并的 `feat/*` 分支残留（该删就删）
- [ ] 后端 `.env.example` 里新增的变量都写了说明（别人照着才能跑起来）
- [ ] 前端 `VITE_API_BASE` 生产环境是 `/api/v1`（相对路径）
- [ ] 服务器有当天的数据库备份（`ls /opt/geo/backups`）
- [ ] 打 tag 并写一条 Release 说明

## 九、常见问题

| 问题 | 处理 |
|---|---|
| `Permission denied (publickey)` | 密钥没加或加成了单仓 Deploy Key；`ssh -T git@github.com` 看回显 |
| `git push` 卡住 / 超时 | 用了 https 地址，改 `git remote set-url origin git@github.com:...` |
| `Updates were rejected` | 远程有你本地没有的提交：`git pull --rebase origin main` 再推 |
| 误提交了 `.env` 或密钥 | 立刻换密钥/密码，再用 `git rm --cached .env` + 提交；历史里的残留需 `git filter-repo` 清理 |
| Actions 不触发 | 检查 yml 缩进、`on:` 分支名是否写了 `master`（我们是 `main`） |
| Actions 额度 | 私仓每月 2000 分钟免费，两人项目远远够；超了先看是不是 workflow 触发条件太宽 |
| 提交在 GitHub 不显示为你的 | `git config user.email` 要等于账号里已验证的邮箱 |
