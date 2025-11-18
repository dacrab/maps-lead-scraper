#!/bin/bash
set -e

# Railway provides the PORT variable at runtime.
# If it's not set, we default to 8000 (for local testing).
SERVER_PORT="${PORT:-8000}"

echo "=================================================="
echo "   STARTING EMAIL SCRAPER CONTAINER"
echo "=================================================="
echo "[*] Detected PORT: $SERVER_PORT"

# 1. Start the Scraper in the BACKGROUND
echo "[*] Launching Scraper in background..."
# Redirect scraper logs to stdout so they appear in Railway logs
python3 /app/scraper.py --config /app/config.json > /proc/1/fd/1 2>&1 &

# 2. Start the Web Server (Gunicorn) in the FOREGROUND
# We use 0.0.0.0 to accept external connections
echo "[*] Starting Web Dashboard on 0.0.0.0:$SERVER_PORT..."
exec gunicorn app:app \
    --bind "0.0.0.0:$SERVER_PORT" \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -

