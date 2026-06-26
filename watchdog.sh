#!/bin/bash
# Passenger self-healing watchdog.
# Run every 5 minutes via cPanel cron:
#   */5 * * * * /home/brzychu/bldg/grant-docs/watchdog.sh >> /home/brzychu/bldg/grant-docs/logs/watchdog.log 2>&1

APP_URL="https://brzychu.cfolks.pl/bldg3/ping"
RESTART_FILE="/home/brzychu/bldg/grant-docs/tmp/restart.txt"
LOG_DIR="/home/brzychu/bldg/grant-docs/logs"

mkdir -p "$LOG_DIR"

HTTP_CODE=$(curl -sf --max-time 10 -o /dev/null -w "%{http_code}" "$APP_URL" 2>/dev/null)

if [ "$HTTP_CODE" = "200" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] OK (HTTP 200)"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL (HTTP ${HTTP_CODE:-timeout}) — triggering Passenger restart"
    touch "$RESTART_FILE"
fi
