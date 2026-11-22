#!/usr/bin/env bash
# 一条命令更新线上后端。放在 /opt/geo/deploy.sh，chmod +x 后执行。
#
#   ./deploy.sh            拉代码 + 装依赖 + 重启 + 自检
#   ./deploy.sh --no-pull  只重启（改了 .env 之后用这个）
#
# 前端在 Vercel 上，由前端自己推，不在这里构建——所以这个脚本比早期那版短很多。
set -euo pipefail

BACKEND=/opt/geo/backend
cd "$BACKEND"

if [[ "${1:-}" != "--no-pull" ]]; then
    echo "==> 拉代码"
    git pull --ff-only
fi

echo "==> 装依赖"
"$BACKEND/.venv/bin/pip" install -e . -q

echo "==> 跑测试（挡掉明显坏掉的版本）"
# 生产只装运行依赖（pip install -e .），没有 pytest。想在这里卡测试就
# 额外装 dev 依赖：.venv/bin/pip install -e ".[dev]"
if "$BACKEND/.venv/bin/python" -m pytest --version >/dev/null 2>&1; then
    "$BACKEND/.venv/bin/python" -m pytest -q -p no:warnings || {
        echo "!! 测试没过，已中止。真要强行发版就先修好测试。" >&2
        exit 1
    }
else
    echo "   没装 pytest，跳过测试（服务器上只装了运行依赖）"
fi

echo "==> 重启 API"
sudo /usr/bin/systemctl restart geo-api

echo "==> schema 体检"
# 不阻断发版：表结构由 SQL 脚本单独管，和代码不是同一次变更。
# 但这项一旦 FAIL，前端很可能已经白屏或数字漂移，必须立刻看。
"$BACKEND/.venv/bin/python" -m app.cli verify || echo "!! schema 体检未全过，见上面明细" >&2

echo "==> 健康检查"
for _ in $(seq 1 10); do
    if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null; then
        echo "OK  API 已就绪"
        curl -fsS http://127.0.0.1:8000/api/v1/ready || true
        echo
        exit 0
    fi
    sleep 1
done

echo "!! 10 秒内没起来，看日志：journalctl -u geo-api -n 80 --no-pager" >&2
exit 1
