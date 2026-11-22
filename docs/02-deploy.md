# 服务器部署手册

> 目标：把**后端采集与 API** 部署到一台 Linux 服务器，每天自动采集、随时能被前端调用。
> 数据库和登录不在服务器上，在 Supabase；前端页面在 Vercel。
> 适用：Ubuntu 22.04 / 24.04，**1 核 1G 就够**（这台机器不建数据库、不构建前端）。

## 一、现在这套东西各自在哪跑

```
        用户浏览器
             │
    ┌────────┴─────────┐
    │                  │
    ▼                  ▼
Vercel            api.geo.example.com
Next.js 前端            │  Nginx :443
    │                   ▼
    │            ┌──────────────┐
    │            │ geo-api      │  FastAPI，只监听 127.0.0.1:8000
    │            └──────────────┘
    │                   │
    │            ┌──────────────┐
    │            │geo-collect   │  每天 02:30 跑一次（systemd timer）
    │            └──────────────┘
    │                   │
    └────────┬──────────┘
             ▼
    Supabase（Postgres + Auth + RLS）
```

| 东西 | 跑在哪 | 谁负责 |
|---|---|---|
| 前端页面 | Vercel | 前端 |
| 数据库、登录、权限 | Supabase | 前端执行 SQL，我们出 SQL |
| 看板读数据 | 前端直连 Supabase（anon key + RLS） | 前端 |
| 采集、算指标、触发接口 | **本文这台服务器** | 后端 |

两个关键判断，先说清楚，免得照老文档装错：

1. **这台机器不装 Postgres。** 数据库在 Supabase，服务器只是它的一个客户端。装本地库会多一份要备份、要升级、会跟线上数据对不上的东西。
2. **这台机器不构建前端。** 前端推 Vercel 自动构建，我们不用 Node、不用 pnpm。

服务器要准备的东西：

| 项 | 说明 | 示例 |
|---|---|---|
| 服务器 | Ubuntu 22.04+，1C1G 起，有公网 IP | `1.2.3.4` |
| 域名 | 一条 A 记录指向服务器 | `api.geo.example.com` |
| 端口 | 安全组放行 22 / 80 / 443 | — |
| Supabase 连接串 | Session pooler 那条（见第四节） | — |
| 引擎密钥 | 至少一家大模型的 API key | `DEEPSEEK_API_KEY` |

下文统一用这两个变量，执行前替换：

```bash
SERVER_IP=1.2.3.4
DOMAIN=api.geo.example.com
```

## 二、首次部署时间线

第一次约 30 分钟，比老文档短很多——不建库、不装 Node、不构建前端。

| 阶段 | 耗时 | 验证动作 | 期望结果 |
|---|---|---|---|
| DNS + 安全组 | 5 分钟 | `ping api.geo.example.com` | 返回服务器 IP |
| 建用户 / 禁 root | 5 分钟 | 新终端 `ssh deploy@IP` | 能登录，`whoami` 是 deploy |
| 装依赖 | 3 分钟 | `python3 -V && nginx -v` | 都打印版本号 |
| 拉代码 + venv | 3 分钟 | `.venv/bin/python -c "import fastapi"` | 无报错 |
| 配 `.env` | 3 分钟 | `python -m app.cli check` | 打印 6 张表的行数 |
| schema 体检 | 1 分钟 | `python -m app.cli verify` | 全部 `[OK]` |
| systemd | 3 分钟 | `curl 127.0.0.1:8000/api/v1/health` | `{"code":"ok",...}` |
| Nginx | 2 分钟 | `curl -I http://api.geo.example.com` | `200` |
| HTTPS | 3 分钟 | `curl https://api.geo.example.com/health` | `{"code":"ok",...}` |

**验证不过不要往下走**，错误会累积到最后难以定位。

## 三、初始化服务器（root 执行一次）

### 1. 建运维用户，禁用 root 直连

```bash
adduser deploy
usermod -aG sudo deploy
# 本机执行：ssh-copy-id deploy@SERVER_IP
```

确认 `ssh deploy@SERVER_IP` 能登录后，改 `/etc/ssh/sshd_config`：

```
PermitRootLogin no
PasswordAuthentication no
```

```bash
systemctl restart ssh
```

### 2. 基础依赖

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl nginx python3 python3-venv python3-pip ufw

python3 -V          # 需要 >= 3.11
```

**不需要装**：Postgres、Node、pnpm、Docker。

### 3. 加 swap（1G 内存必做）

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 四、拉代码与配置

### 1. 只读部署密钥

不要把个人私钥放服务器上，给仓库配一把只能读的 Deploy Key：

```bash
ssh-keygen -t ed25519 -C "server@geo" -f ~/.ssh/geo_backend -N ""
cat ~/.ssh/geo_backend.pub
```

贴到 GitHub → 后端仓 → Settings → Deploy keys → Add deploy key（**不勾** Allow write access）。

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-backend
  HostName github.com
  User git
  IdentityFile ~/.ssh/geo_backend
  IdentitiesOnly yes
EOF

sudo mkdir -p /opt/geo && sudo chown -R deploy:deploy /opt/geo
git clone git@github-backend:<你>/GEO-Monitor.git /opt/geo/backend
mkdir -p /opt/geo/backups
```

### 2. 虚拟环境

```bash
cd /opt/geo/backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e . -q       # 只装生产依赖
.venv/bin/python -c "import fastapi, asyncpg, jwt; print('依赖 OK')"
```

### 3. 生产 `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

必须改的项：

```ini
ENV=prod
DEBUG=false
LOG_JSON=true
LOG_LEVEL=INFO

# 只监听本机，外部一律走 Nginx
HOST=127.0.0.1
PORT=8000

# Supabase Session pooler（Project Settings → Database → Connection string → Session pooler）
DATABASE_URL=postgresql://postgres.<ref>:<密码>@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres

# 只用于校验前端带来的 token，填上就行，不用找 secret
SUPABASE_URL=https://<ref>.supabase.co

# 前端 Vercel 域名；预览域名用正则兜住
CORS_ORIGINS=https://geo-monitor.vercel.app
CORS_ORIGIN_REGEX=^https://geo-monitor(-[a-z0-9-]+)?\.vercel\.app$

REPORT_TIMEZONE=Asia/Shanghai
```

三条容易踩的：

- **用 pooler 串，别用 `db.xxx.supabase.co` 直连串。** 直连域名在部分网络里只有 IPv6，会报 `ENETUNREACH`；我们这边实测在 WSL 里就是这样。
- **串里带 `?sslmode=require` 不用删。** 代码会自动翻译成 asyncpg 的参数，原样粘贴即可。
- **不要写 `JWT_SECRET`。** 那是早期自签方案的遗留，现在 token 由 Supabase 签，写了也不起作用（会被忽略，但容易让人误以为鉴权配错了）。

`CORS_ORIGINS` 里写的是**前端实际域名**，写错了浏览器会直接拦请求并报 CORS 错，日志里看不到，因为请求根本没到。

### 4. 第一次自检

```bash
cd /opt/geo/backend

# 连得上库吗、6 张表在不在
.venv/bin/python -m app.cli check

# schema 体检：视图、security_invoker、唯一约束、只读策略
.venv/bin/python -m app.cli verify

# 哪些引擎配了密钥
.venv/bin/python -m app.cli engines
```

`check` 报缺表 → 说明 `docs/sql/01-schema.sql` 或 `02-auth.sql` 还没跑完，去 Supabase SQL Editor 补。
`verify` 报某项为 `[FAIL]`，detail 里写了该跑哪份 SQL，不要跳过——视图 `security_invoker` 掉了会变成绕过 RLS，客户之间能互相看到数据。

想先确认链路通不通、又不想花钱：

```bash
.venv/bin/python -m app.cli seed                 # 灌 7 天假数据
.venv/bin/python -m app.cli collect --all --dry-run --limit 1   # 真调引擎但不写库
```

## 五、systemd：常驻服务 + 每日采集

单元文件都在仓库 `deploy/` 下，直接拷：

```bash
cd /opt/geo/backend
sudo cp deploy/geo-api.service deploy/geo-collect.service deploy/geo-collect.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now geo-api geo-collect.timer
```

验证：

```bash
systemctl status geo-api --no-pager
curl -s http://127.0.0.1:8000/api/v1/health
systemctl list-timers geo-collect.timer --no-pager    # 看下次触发时间
```

手动跑一轮采集（前台等它结束，约 5–10 分钟）：

```bash
sudo systemctl start geo-collect.service
journalctl -u geo-collect -n 80 --no-pager
```

**为什么采集用 timer 而不是 API 进程内定时器**：API 会被重启（发版、OOM），进程内的定时任务跟着没；timer 是系统级的，重启后 `Persistent=true` 还会补跑错过的点。API 里那个 `/collect` 接口只用于「手动补跑一次」。

`--all` 是逐个项目跑，单个项目失败不影响后面的；退出码非 0 时 `systemctl status` 会显示 failed，很好发现。

## 六、Nginx 与 HTTPS

DNS 先加 A 记录：`api` → `SERVER_IP`，等 `ping api.geo.example.com` 通了再申请证书。

```bash
sudo cp /opt/geo/backend/deploy/nginx-api.conf /etc/nginx/sites-available/geo-api
sudo sed -i "s/api\.geo\.example\.com/$DOMAIN/" /etc/nginx/sites-available/geo-api
sudo ln -sf /etc/nginx/sites-available/geo-api /etc/nginx/sites-enabled/geo-api
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

配置文件里对外只开 `/api/` 和 `/health`，其他一律 404——这台机器不托管前端，没必要暴露 `/docs`。

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.geo.example.com --redirect -m 你的邮箱 --agree-tos -n
sudo certbot renew --dry-run
```

```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
sudo ufw status
```

## 七、告诉前端两个地址

前端不需要知道我们服务器的 IP，只需要：

1. **API 地址**：`https://api.geo.example.com/api/v1`，配到 Vercel 环境变量（如 `NEXT_PUBLIC_API_BASE`）。
2. **后端已支持 Vercel 预览域名**：`CORS_ORIGIN_REGEX` 已配，PR 预览不用每次加白名单。

唯一的例外是 Supabase 的 Redirect URL——登录回调走 Supabase，不走我们，必须在前端那边把 Vercel 域名加进 Supabase 的 URL Configuration。

## 八、备份

数据库在 Supabase，备份分两层：

1. **Supabase 自带**：Free 计划没有自动备份，Pro 有每日备份（Project Settings → Database → Backups）。演示前确认一下计划。
2. **自己再打一份**（不依赖任何计划）：

```bash
# 只装客户端（约 30MB），不装服务端、不启动任何服务——服务器上仍然没有本地数据库
sudo apt install -y postgresql-client

mkdir -p /opt/geo/backups
chmod 600 /opt/geo/backend/deploy/backup.sh
crontab -e
```

```cron
# 每周一 04:00 导出一次，保留 8 周。脚本自己从 .env 读连接串
0 4 * * 1 /opt/geo/backend/deploy/backup.sh >> /opt/geo/backups/backup.log 2>&1
```

`deploy/backup.sh` 从 `.env` 里读 `DATABASE_URL`（自动去掉 `+asyncpg` 后缀）调 `pg_dump`，失败会以非 0 退出。恢复演练至少做一次——出事时手会抖：

```bash
gunzip -c /opt/geo/backups/geo-2026-11-22.sql.gz | psql "<连接串>"
```

## 九、更新与回滚

```bash
/opt/geo/deploy.sh               # 拉代码 + 装依赖 + 跑测试 + 重启 + 自检
/opt/geo/deploy.sh --no-pull     # 只重启（改了 .env 之后用）
```

脚本会跑 `pytest`，测试不过直接中止——比发上去再回滚便宜。`verify` 那一步不阻断发版，因为表结构由 SQL 脚本单独管，和代码不是同一次变更；但它一旦 FAIL 必须立刻看。

免密重启（只放开这一条命令，别给全量 sudo）：

```bash
sudo visudo -f /etc/sudoers.d/geo
```

```
deploy ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart geo-api, /usr/bin/systemctl status geo-api
```

回滚：

```bash
cd /opt/geo/backend
git log --oneline -10
git checkout <上一个好版本>
sudo systemctl restart geo-api
```

前端回滚在 Vercel 后台点 Redeploy / Promote，不归这台机器。

## 十、部署验收清单

- [ ] `curl https://api.geo.example.com/health` 返回 `{"code":"ok",...}`
- [ ] `curl https://api.geo.example.com/api/v1/ready` 返回 `database: ok`，没有缺表
- [ ] `.venv/bin/python -m app.cli verify` 全 `[OK]`
- [ ] `systemctl status geo-api` 是 active (running)，日志是 JSON 单行
- [ ] `systemctl list-timers geo-collect.timer` 显示下次触发时间
- [ ] `sudo systemctl start geo-collect.service` 能跑完，`journalctl` 里有「采集结束 | 成功 N」
- [ ] 前端页面能看到当天数字（说明 Supabase 那边视图和 RLS 也通了）
- [ ] 前端点「采集」按钮返回 202，`/runs` 能看到新批次
- [ ] `sudo reboot` 后服务和 timer 都自动起来
- [ ] `crontab -l` 里有备份任务

## 十一、排错对照表

| 现象 | 原因 | 排查 |
|---|---|---|
| `ENETUNREACH` 连不上库 | 用了 `db.xxx.supabase.co` 直连串，该域名只有 IPv6 | 换成 Session pooler 串 |
| `unrecognized configuration parameter "sslmode"` | 老版本代码没做参数翻译 | 更新到最新代码；临时可手动删 `?sslmode=require` |
| `password authentication failed` | 密码里的特殊字符没转义 | 密码含 `@ # / ?` 时用 URL 编码，或换成只含字母数字的密码 |
| 502 Bad Gateway | 后端没起来 | `systemctl status geo-api`；`curl 127.0.0.1:8000/api/v1/health` |
| 浏览器报 CORS，日志里没请求 | `CORS_ORIGINS` 没写前端实际域名 | 改成真实域名；预览域名确认 `CORS_ORIGIN_REGEX` 生效 |
| 接口 401，token 明明刚拿到 | `SUPABASE_URL` 没填或填错项目 | `curl $SUPABASE_URL/auth/v1/health`；确认和前端是同一个项目 |
| `verify` 报视图 security_invoker | 视图被 `create or replace` 过 | 重跑 `docs/sql/02-auth.sql` 里改视图的那几行 |
| 采集任务 1 小时被 systemd 杀掉 | 引擎限流或密钥错，60 个请求全在等重试 | `journalctl -u geo-collect` 看失败原因；修完手动 `start` 一次 |
| timer 不触发 | 单元文件改了没 `daemon-reload` | `systemctl daemon-reload && systemctl restart geo-collect.timer` |
| 看板数字比昨天少了 | 当天采集失败，或跨时区归日 | `.venv/bin/python -m app.cli stats --days 3` 看哪天没数据 |

## 十二、安全底线

1. `.env` 永不入库（`.gitignore` 已覆盖），服务器上 `chmod 600`
2. 数据库不由本机暴露，安全组不放行 5432；服务器也不需要对外开 8000
3. 服务用 `deploy` 普通用户跑，单元文件已带 `NoNewPrivileges` / `ProtectSystem=full`
4. 服务器放的是**只读部署密钥**，不是个人私钥
5. Supabase 的 `service_role` key 不要放进服务器 `.env`：我们用连接串直连，不需要它
6. 定期 `sudo apt upgrade -y`；`unattended-upgrades` 可开

## 十三、后期扩展点

- **采集量上来**：`collect --all` 改成按项目分片（多个 timer 单元，每个 `--project xxx`），避免一轮跑太久
- **任务可靠性**：现在采集是「一个进程跑完」，没有失败重试队列。批量大了之后建议把 collector 拆成 worker，配 Redis 队列
- **多机/容器**：现在单机足够。真要上容器，把 `deploy/` 换成 Dockerfile + compose，`.env` 逻辑不变
