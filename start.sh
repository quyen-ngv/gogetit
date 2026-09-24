#!/bin/sh
set -e

echo "Starting Place Import API on :${PLACE_API_PORT:-8080}..."
python -u api_server.py &
API_PID=$!

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "Starting Telegram bot..."
  python -u telegram_bot.py &
  BOT_PID=$!
else
  echo "TELEGRAM_BOT_TOKEN not set - skipping Telegram bot"
  BOT_PID=""
fi

shutdown() {
  echo "Shutting down..."
  kill "$API_PID" 2>/dev/null || true
  if [ -n "$BOT_PID" ]; then
    kill "$BOT_PID" 2>/dev/null || true
    wait "$BOT_PID" 2>/dev/null || true
  fi
  wait "$API_PID" 2>/dev/null || true
  exit 0
}

trap shutdown TERM INT

if [ -n "$BOT_PID" ]; then
  while kill -0 "$API_PID" 2>/dev/null && kill -0 "$BOT_PID" 2>/dev/null; do
    sleep 2
  done
  echo "A process exited unexpectedly"
  shutdown
  exit 1
fi

wait "$API_PID"
