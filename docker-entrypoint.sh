#!/bin/sh
set -e

HOST="${MYSQL_HOST:-db}"
PORT="${MYSQL_PORT:-3306}"
USER="${MYSQL_USER:-grant_docs}"
PASS="${MYSQL_PASSWORD:-}"
DB="${MYSQL_DATABASE:-grant_docs}"

echo "[entrypoint] Waiting for MySQL schema at ${HOST}:${PORT}..."
until python3 - <<EOF
import pymysql, sys
try:
    c = pymysql.connect(host="$HOST", port=$PORT, user="$USER",
                        password="$PASS", database="$DB", connect_timeout=3)
    cur = c.cursor()
    cur.execute("SHOW TABLES LIKE 'app_settings'")
    if not cur.fetchone():
        print("  schema not ready yet (app_settings missing)")
        c.close()
        sys.exit(1)
    c.close()
    sys.exit(0)
except Exception as e:
    print(f"  not ready: {e}")
    sys.exit(1)
EOF
do
    sleep 2
done

echo "[entrypoint] MySQL schema ready. Starting: $*"
exec "$@"
