#!/bin/sh
# Schedule as the app user. Refuse the MDM root context; no credential needed.
set -eu
export PRELOOP_DISABLE_TELEMETRY=true
if [ "$(id -u)" -eq 0 ]; then
    echo 'Run inventory-user.sh in the intended app user session, not as root.' >&2
    exit 2
fi
cli=${1:-/opt/preloop/bin/preloop}
exec "$cli" agents discover --inventory
