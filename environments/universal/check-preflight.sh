#!/bin/sh
# Fed to the target image over stdin, so even an old embedded smoke is gated.
set -eu

if ! command -v preloop-preflight >/dev/null 2>&1; then
    echo 'universal-smoke: FAIL: missing required tool: preloop-preflight' >&2
    exit 1
fi
preloop-preflight --help >/dev/null
echo 'PASS preloop-preflight'
