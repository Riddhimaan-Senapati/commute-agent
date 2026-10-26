#!/bin/sh
# Start OTP and the API on the container's loopback, wait for the API, then serve Next.js
# on $PORT. The API starts without waiting for OTP: until OTP answers, uncached trips report
# "OTP unreachable" and the demo trips still come from the cache.
set -eu
cd /home/user/app

# The same entrypoint the OTP image uses, pointed at our graph directory.
java $OTP_JAVA_OPTS -cp @/app/jib-classpath-file @/app/jib-main-class-file \
  /home/user/app/data/otp/ --load --serve &
OTP_PID=$!

/home/user/app/.venv/bin/python -m commute_agent.api --host 127.0.0.1 --port 8000 &
API_PID=$!

i=0
until curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 300 ] || ! kill -0 "$API_PID" 2>/dev/null; then
    echo "API failed to start" >&2
    exit 1
  fi
  sleep 1
done
echo "API ready after ${i}s"
kill -0 "$OTP_PID" 2>/dev/null || echo "OTP is not running; uncached trips have no transit" >&2

cd /home/user/app/webapp
exec /home/user/app/webapp/node_modules/.bin/next start -H 0.0.0.0 -p "$PORT"
