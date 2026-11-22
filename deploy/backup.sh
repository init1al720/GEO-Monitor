#!/usr/bin/env bash
# 用 .env 里的连接串导出 Supabase 数据库，gzip 保存，只留最近 8 周。
# 需要 postgresql-client（只装客户端，不装服务端，不启动任何服务）。
#
# 密码在连接串里，所以这个文件的权限要 600，日志也别打到公开的地方。
set -euo pipefail

BACKEND=${BACKEND:-/opt/geo/backend}
OUT=${OUT:-/opt/geo/backups}
KEEP_DAYS=${KEEP_DAYS:-56}

mkdir -p "$OUT"

RAW=$(grep -E '^DATABASE_URL=' "$BACKEND/.env" | head -1 | cut -d= -f2-)
if [[ -z "${RAW}" ]]; then
    echo "$(date -Is) 没在 $BACKEND/.env 里找到 DATABASE_URL" >&2
    exit 1
fi

# SQLAlchemy 的驱动后缀 pg_dump 不认：postgresql+asyncpg:// → postgresql://
URL=${RAW/postgresql+asyncpg:\/\//postgresql:\/\/}
case "$URL" in
    postgresql://* | postgres://*) ;;
    *)
        echo "$(date -Is) DATABASE_URL 不是 Postgres 串（${URL%%:*}），跳过" >&2
        exit 0
        ;;
esac

FILE="$OUT/geo-$(date +%F).sql.gz"
pg_dump "$URL" --no-owner --no-privileges --no-comments | gzip >"$FILE.tmp"
mv "$FILE.tmp" "$FILE"

find "$OUT" -name 'geo-*.sql.gz' -mtime "+$KEEP_DAYS" -delete

echo "$(date -Is) 备份完成 $(du -h "$FILE" | cut -f1) $FILE"
