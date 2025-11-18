#!/bin/bash
set -e

# Force port 8000 if not set
SERVER_PORT="${PORT:-8000}"

echo "=================================================="
echo "   STARTING EMAIL SCRAPER CONTAINER"
echo "   BINDING TO PORT: $SERVER_PORT"
echo "=================================================="

# 1. Start the Web Server (Gunicorn) in the BACKGROUND
# We start this FIRST so Railway sees the port is open immediately.
echo "[*] Starting Web Dashboard..."
gunicorn app:app \
    --bind "0.0.0.0:$SERVER_PORT" \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - &

WEB_PID=$!

# 2. Launch Scraper in the BACKGROUND
echo "[*] Launching Scraper..."
python3 /app/scraper.py --config /app/config.json > /proc/1/fd/1 2>&1 &

# 3. Wait for the Web Server
# This keeps the container running and the port open.
wait $WEB_PID

