#!/bin/bash
# Decide whether a Publish Universal Image run builds, and whether it pushes.
#
# Pull requests and the weekly schedule build and smoke only. A push to main
# publishes only when the recipe changed (RECIPE=true). workflow_dispatch
# publishes only on main. A v* tag publishes. Anything else does not push.
#
# Prints:
#   publish=true|false
#   run=true|false
set -euo pipefail

publish=false
run=true
event="${GITHUB_EVENT_NAME:-}"
ref="${GITHUB_REF:-}"
ref_type="${GITHUB_REF_TYPE:-}"
ref_name="${GITHUB_REF_NAME:-}"
recipe="${RECIPE:-}"

if [ "$event" = "push" ] && [ "$ref" = "refs/heads/main" ]; then
    if [ "$recipe" = "true" ]; then
        publish=true
    else
        run=false
    fi
elif [ "$event" = "push" ] && [ "$ref_type" = "tag" ]; then
    case "$ref_name" in
        v*) publish=true ;;
        *) run=false ;;
    esac
elif [ "$event" = "workflow_dispatch" ] && [ "$ref" = "refs/heads/main" ]; then
    publish=true
fi

printf 'publish=%s\nrun=%s\n' "$publish" "$run"
