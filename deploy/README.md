# deploy/

线上后端用的 systemd 单元与部署脚本。**照着 `docs/02-deploy.md` 做就行**，这里只解释每个文件管什么。

| 文件 | 装到哪 | 作用 |
|---|---|---|
| `geo-api.service` | `/etc/systemd/system/` | 常驻 FastAPI，只监听 127.0.0.1:8000 |
| `geo-collect.service` | `/etc/systemd/system/` | 一次性任务：`collect --all`，跑完退出 |
| `geo-collect.timer` | `/etc/systemd/system/` | 每天北京时间 02:30 触发上面那个任务 |
| `nginx-api.conf` | `/etc/nginx/sites-available/geo-api` | 只对外暴露 `api.<域名>` 的 `/api/` 与 `/health` |
| `deploy.sh` | `/opt/geo/deploy.sh` | 拉代码 + 装依赖 + 跑测试 + 重启 + 自检 |
| `backup.sh` | 留在仓库里被 cron 调用 | `pg_dump` 导出 Supabase 数据库，保留 8 周 |

安装（服务器上，首次）：

```bash
sudo cp deploy/geo-api.service deploy/geo-collect.service deploy/geo-collect.timer \
     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now geo-api geo-collect.timer

sudo cp deploy/nginx-api.conf /etc/nginx/sites-available/geo-api
sudo ln -sf /etc/nginx/sites-available/geo-api /etc/nginx/sites-enabled/geo-api
sudo nginx -t && sudo systemctl reload nginx

sudo cp deploy/deploy.sh /opt/geo/deploy.sh && sudo chmod +x /opt/geo/deploy.sh
```

改完单元文件必须 `daemon-reload`，否则 systemd 还在用旧的。

常用排查：

```bash
systemctl list-timers geo-collect.timer --no-pager   # 下次什么时候跑
systemctl start geo-collect.service                  # 手动跑一轮（前台等它结束）
journalctl -u geo-collect -n 80 --no-pager           # 上次采集干了什么
journalctl -u geo-api -f                             # 跟 API 日志
```

> 两台机器上的时区不一致是最容易踩的坑：timer 里写的是带时区的 `OnCalendar`，采集服务的 `TZ=Asia/Shanghai` 只影响日志时间戳，指标归日看的是 `.env` 里的 `REPORT_TIMEZONE`。三者独立，但都该指向北京时间。
