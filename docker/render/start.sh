#!/bin/sh
# Start the API on the container's loopback, wait for it, then serve Next.js on $PORT.
set -eu
cd /app
# Render gives the OTP private service as host:port; the planner wants the GraphQL URL.
if [ -n "${COMMUTE_OTP_HOSTPORT:-}" ] && [ -z "${COMMUTE_OTP_URL:-}" ]; then
  export COMMUTE_OTP_URL="http://${COMMUTE_OTP_HOSTPORT}/otp/gtfs/v1"
fi
/app/.venv/bin/python -m commute_agent.api --host 127.0.0.1 --port 8000 &
API_PID=$!

i=0
until curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 180 ] || ! kill -0 "$API_PID" 2>/dev/null; then
    echo "API failed to start" >&2
    exit 1
  fi
  sleep 1
done
echo "API ready after ${i}s"

cd /app/webapp
exec /app/webapp/node_modules/.bin/next start -H 0.0.0.0 -p "$PORT"
