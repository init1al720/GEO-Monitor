# 服务器部署手册

> 目标：把后端 + 前端部署到一台 Linux 云服务器，用域名 HTTPS 访问，之后一条命令更新。
> 适用：Ubuntu 22.04 / 24.04，2 核 2G 起步（低于 2G 必须先加 swap）。

## 一、部署架构

```
用户浏览器
    │  https://geo.example.com
    ▼
┌─────────────────────────────────────────┐
│ Nginx :443（TLS 终止、静态托管、反代）    │
│   /        → /var/www/geo（前端 dist）   │
│   /api/    → 127.0.0.1:8000              │
└─────────────────────────────────────────┘
                    │
                    ▼
        uvicorn（systemd: geo-api，2 worker）
                    │
                    ▼
        PostgreSQL 127.0.0.1:5432（geo 库）
```

关键决策：**前后端同域部署**，前端请求走相对路径 `/api/v1`。这样生产环境不需要 CORS，也省掉一堆跨域排查。

需要提前准备的东西：

| 项 | 说明 | 示例 |
|---|---|---|
| 服务器 | Ubuntu 22.04+，有公网 IP，能 SSH | `1.2.3.4` |
| 域名 | A 记录指向服务器 IP | `geo.example.com` |
| 端口 | 安全组放行 22 / 80 / 443 | — |
| 仓库 | 后端私仓已推到 GitHub | `github.com/<你>/geo-backend` |
| 前端仓 | 学弟的前端私仓 | `github.com/<学弟>/geo-frontend` |

下文命令统一用这些变量，执行前替换掉：

```bash
SERVER_IP=1.2.3.4
DOMAIN=geo.example.com
```

## 二、第一步：DNS 与安全组

1. 域名控制台加一条 A 记录：`geo` → `SERVER_IP`，TTL 默认
2. 云服务器安全组放行 `22`、`80`、`443`（**不要**放行 5432 和 8000）
3. 本机验证解析生效：`ping geo.example.com`

> DNS 没生效就先别申请证书，certbot 会校验失败。解析生效一般 1–10 分钟。

## 三、初始化服务器（用 root 执行一次）

### 1. 建运维用户，禁用 root 直连

```bash
adduser deploy
usermod -aG sudo deploy
# 把本机公钥给 deploy 用户（本机执行 ssh-copy-id deploy@SERVER_IP）
```

确认 `ssh deploy@SERVER_IP` 能登录后，编辑 `/etc/ssh/sshd_config`：

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
sudo apt install -y git curl nginx postgresql postgresql-contrib python3 python3-venv python3-pip ufw

# Node 20（前端构建用）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm i -g pnpm

# 版本自检：Python 需 >= 3.11
python3 -V && node -v && pnpm -v
```

### 3. 加 swap（内存 2G 及以下必做，否则 `pnpm build` 会被 OOM 杀掉）

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 四、数据库

```bash
sudo -u postgres psql <<'SQL'
CREATE USER geo WITH PASSWORD '换成你的强密码';
CREATE DATABASE geo OWNER geo ENCODING 'UTF8';
SQL

# 验证（能连上并看到 geo= 就对了）
psql "postgresql://geo:你的密码@127.0.0.1:5432/geo" -c '\conninfo'
```

Postgres 默认只监听 `127.0.0.1`，这正是我们要的——数据库不暴露到公网。

## 五、拉代码（服务器上配只读部署密钥）

服务器要能拉私仓，但**不要**把你的个人 SSH 私钥放到服务器上。每个仓库配一把独立的只读 Deploy Key：

```bash
# 服务器上生成
ssh-keygen -t ed25519 -C "server@geo" -f ~/.ssh/geo_backend -N ""
ssh-keygen -t ed25519 -C "server@geo" -f ~/.ssh/geo_frontend -N ""
cat ~/.ssh/geo_backend.pub
```

把 `geo_backend.pub` 内容贴到后端仓：GitHub → 仓库 → Settings → Deploy keys → Add deploy key（**不勾** Allow write access）。
`geo_frontend.pub` 同理贴到前端仓。

写 `~/.ssh/config` 指定用哪把钥匙：

```
Host github-backend
  HostName github.com
  User git
  IdentityFile ~/.ssh/geo_backend
  IdentitiesOnly yes

Host github-frontend
  HostName github.com
  User git
  IdentityFile ~/.ssh/geo_frontend
  IdentitiesOnly yes
```

```bash
mkdir -p /opt/geo && sudo chown -R deploy:deploy /opt/geo
cd /opt/geo
git clone git@github-backend:<你>/geo-backend.git backend
git clone git@github-frontend:<学弟>/geo-frontend.git frontend
```

`/opt/geo` 下的目录结构：

```
/opt/geo/
├── backend/          # 后端仓库
├── frontend/         # 前端仓库
├── backups/          # 数据库备份
└── logs/             # 需要文件日志时用
```

## 六、后端：依赖、配置、迁移

```bash
cd /opt/geo/backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .        # 只装生产依赖，不装 dev
```

> 若 `pyproject.toml` 里还没加 `asyncpg`，现在补上：`asyncpg>=0.29`，Postgres 异步驱动必需。

生成生产配置：

```bash
cp .env.example .env
openssl rand -hex 32          # 把输出填到 JWT_SECRET
nano .env
```

生产 `.env` 关键项（其余保持默认）：

```ini
ENV=prod
DEBUG=false
LOG_JSON=true
LOG_LEVEL=INFO
HOST=127.0.0.1
PORT=8000
DATABASE_URL=postgresql+asyncpg://geo:你的密码@127.0.0.1:5432/geo
JWT_SECRET=上一步生成的32字节hex
CORS_ORIGINS=https://geo.example.com
```

注意 `HOST=127.0.0.1` —— 只监听本机，外部一律走 Nginx。

跑迁移和种子数据：

```bash
cd /opt/geo/backend
.venv/bin/alembic upgrade head
.venv/bin/python -m app.scripts.seed     # 首次部署灌演示数据，生产可跳过
```

## 七、后端：systemd 托管

```bash
sudo nano /etc/systemd/system/geo-api.service
```

```ini
[Unit]
Description=GEO API (FastAPI)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/opt/geo/backend
ExecStart=/opt/geo/backend/.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 \
    --workers 2 \
    --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

# 基础加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/geo/backend

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now geo-api
sudo systemctl status geo-api --no-pager
curl -s http://127.0.0.1:8000/api/v1/health
journalctl -u geo-api -n 50 --no-pager      # 看日志
```

> 配置读的是 `WorkingDirectory` 下的 `.env`（pydantic-settings 自动加载），所以**不需要**在 unit 里写 `EnvironmentFile`——那样反而会被 systemd 的引号规则坑到。

## 八、前端：构建到 Nginx 目录

先确认前端生产环境的接口地址用的是**相对路径**（同域部署，避免跨域）：

```
# frontend/.env.production
VITE_API_BASE=/api/v1
```

```bash
cd /opt/geo/frontend
pnpm install --frozen-lockfile
pnpm build                      # 产物在 dist/

sudo mkdir -p /var/www/geo
sudo chown deploy:deploy /var/www/geo
rsync -a --delete dist/ /var/www/geo/
```

## 九、Nginx

```bash
sudo nano /etc/nginx/sites-available/geo
```

```nginx
server {
    listen 80;
    server_name geo.example.com;

    root /var/www/geo;
    index index.html;

    # 前端单页应用：刷新子路由不能 404
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 带 hash 的静态资源长缓存
    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # 后端接口
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;    # W2 的采集接口会慢，先留足
    }

    client_max_body_size 20m;
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/geo /etc/nginx/sites-enabled/geo
sudo rm -f /etc/nginx/sites-enabled/default      # 去掉默认站点，避免 80 抢路
sudo nginx -t && sudo systemctl reload nginx
```

## 十、HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d geo.example.com --redirect -m 你的邮箱 --agree-tos -n
sudo certbot renew --dry-run        # 验证自动续期
```

申请成功后 certbot 会自动改 Nginx 配置加 443 和 80→443 跳转并重载。

## 十一、防火墙

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

## 十二、备份（别跳过）

```bash
mkdir -p /opt/geo/backups
crontab -e
```

```cron
# 每天 3 点备份数据库，保留 14 天
0 3 * * * pg_dump "postgresql://geo:你的密码@127.0.0.1:5432/geo" | gzip > /opt/geo/backups/geo-$(date +\%F).sql.gz && find /opt/geo/backups -name 'geo-*.sql.gz' -mtime +14 -delete
```

恢复演练（至少做一次，否则出事时手会抖）：

```bash
gunzip -c /opt/geo/backups/geo-2026-11-20.sql.gz | psql "postgresql://geo:密码@127.0.0.1:5432/geo"
```

## 十三、更新与回滚

### 一条命令更新

`/opt/geo/deploy.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "==> 后端"
cd /opt/geo/backend
git pull --ff-only
.venv/bin/pip install -e . -q
.venv/bin/alembic upgrade head
sudo /usr/bin/systemctl restart geo-api

echo "==> 前端"
cd /opt/geo/frontend
git pull --ff-only
pnpm install --frozen-lockfile
pnpm build
rsync -a --delete dist/ /var/www/geo/

echo "==> 健康检查"
sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/health && echo " OK"
```

```bash
chmod +x /opt/geo/deploy.sh
```

免密重启（只放开这一条命令，别给全量 sudo）：

```bash
sudo visudo -f /etc/sudoers.d/geo
```

```
deploy ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart geo-api, /usr/bin/systemctl status geo-api
```

### 回滚

```bash
cd /opt/geo/backend && git log --oneline -5      # 找到上一个好版本
git checkout <commit> && sudo systemctl restart geo-api
# 前端同理，然后重新 pnpm build + rsync
```

数据库回滚：迁移有 `downgrade` 才可退，否则从备份恢复。**每周演示前打一次备份**，比什么都实在。

## 十四、部署验收清单

- [ ] `https://geo.example.com` 打开是前端登录页，地址栏有锁
- [ ] `http://geo.example.com` 自动跳 https
- [ ] `https://geo.example.com/api/v1/health` 返回 `{"code":"ok",...}`
- [ ] 登录成功，刷新页面不掉登录态
- [ ] 直接访问 `https://geo.example.com/projects/p_1` 刷新不 404
- [ ] 浏览器控制台无跨域报错
- [ ] `sudo systemctl status geo-api` 是 active (running)，`journalctl` 里日志是 JSON 单行
- [ ] 服务器重启后服务自动起来（`sudo reboot` 试一次）
- [ ] `crontab -l` 里有备份任务

## 十五、排错对照表

| 现象 | 原因 | 排查 |
|---|---|---|
| 502 Bad Gateway | 后端没起来或端口不对 | `systemctl status geo-api`；`curl 127.0.0.1:8000/api/v1/health` |
| 刷新子路由 404 | Nginx 缺 `try_files ... /index.html` | 检查 location / 配置 |
| 页面空白但接口 200 | 前端 `VITE_API_BASE` 打包时没生效 | 重新 `pnpm build`，确认 `.env.production` |
| 接口 500，日志有 CSRF/连接错 | `DATABASE_URL` 密码或驱动错 | 用 psql 手动连一次验证；确认装了 `asyncpg` |
| 证书申请失败 | DNS 未生效或 80 被占用 | `ping` 域名；`nginx -t`；临时停 Nginx 试 |
| `alembic upgrade` 报 relation 不存在 | 没跑迁移就到新版本代码 | 先迁移再重启 |
| 构建被 Killed | 内存不足 | 加 swap，或本地构建后只传 `dist/` |

## 十六、安全底线

1. `.env` 永不入库（`.gitignore` 已覆盖），服务器上 `chmod 600 .env`
2. 数据库只监听 127.0.0.1，安全组不放行 5432
3. 服务用 `deploy` 普通用户跑，不用 root
4. 服务器放的是**专用部署密钥**，不是你的个人私钥；仓库只能读
5. `JWT_SECRET` 必须换成随机值，默认值等于没有鉴权
6. 定期 `sudo apt upgrade -y`，`unattended-upgrades` 可开

## 十七、W2 之后的扩展点

- **采集任务**需要长时间运行：现在是 systemd 起的 API 进程内 APScheduler，量大了要拆出独立 worker 进程（同一个仓库，`python -m app.worker`，再建一个 systemd unit）
- **采集接口超时**：Nginx `proxy_read_timeout` 已设 180s，但仍建议采集走「提交任务 + 轮询进度」，不要让 HTTP 请求等结果
- **多机/容器化**：规模上来再上 Docker Compose 或 K8s，现在单机完全够
