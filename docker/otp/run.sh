#!/usr/bin/env bash
# Build or serve the OpenTripPlanner graph for the commute agent.
#
#   docker/otp/run.sh build   # build the graph from data/otp and save it
#   docker/otp/run.sh serve   # load the saved graph and serve on :8080
#
# docker/otp/build-config.json is the source of truth. It is copied into
# data/otp (the OTP base directory) before each run, because Docker Desktop
# cannot bind-mount a single file inside another bind mount on virtiofs.
set -euo pipefail

cmd="${1:-}"
case "$cmd" in build|serve) ;; *) echo "usage: $0 {build|serve}" >&2; exit 2 ;; esac

cp docker/otp/build-config.json data/otp/build-config.json

case "$cmd" in
  build)
    docker run --rm -e JAVA_TOOL_OPTIONS=-Xmx6g \
      -v "$PWD/data/otp:/var/opentripplanner" \
      opentripplanner/opentripplanner:2.10.0 --build --save
    ;;
  serve)
    docker run --rm -e JAVA_TOOL_OPTIONS=-Xmx6g \
      -v "$PWD/data/otp:/var/opentripplanner" \
      -p 8080:8080 \
      opentripplanner/opentripplanner:2.10.0 --load --serve
    ;;
esac
