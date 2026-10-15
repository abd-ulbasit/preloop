#!/bin/sh
# Offline export only. Never installs into a managed settings directory.
set -eu
export PRELOOP_DISABLE_TELEMETRY=true
if [ "$#" -ne 4 ]; then
    echo 'usage: export-overlay.sh CLI OUTPUT PLATFORM TARGET_CLI_PATH' >&2
    exit 2
fi
exec "$1" agents managed-config claude-code \
    --output "$2" --platform "$3" --cli-path "$4" --timeout 300
