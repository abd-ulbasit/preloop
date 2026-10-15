#!/bin/sh
set -eu
export PRELOOP_DISABLE_TELEMETRY=true
if [ "${1:-}" != "--run-app" ] || [ "$#" -ne 2 ]; then
    echo 'Usage: run.sh --run-app NEW_EVIDENCE_DIRECTORY' >&2
    exit 2
fi
# Never mount an existing directory containing host files into the app container.
umask 077
mkdir "$2"
output=$(cd "$2" && pwd)
root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
docker build -f "$root/examples/claude-app-verification/Dockerfile" -t preloop-app-verification:1182 "$root"
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    --read-only --tmpfs /tmp:rw,exec,mode=1777 --pids-limit 256 \
    --user "$(id -u):$(id -g)" \
    -v "$output:/evidence" preloop-app-verification:1182 --run-app
