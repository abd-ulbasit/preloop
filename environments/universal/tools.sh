#!/bin/bash
# shellcheck disable=SC2034
# Commands the universal image smoke requires on PATH.
# run-smoke.sh --negative-tool deletes one of these and expects the smoke
# to fail with "missing required tool: <name>".
UNIVERSAL_TOOLS=(
    mypy
    ruff
    pytest
    javac
    mvn
    go
    golangci-lint
    cargo
    rustc
    cargo-clippy
    tsc
    eslint
    perl
    cpanm
    perlver
    perlcritic
    prove
    shellcheck
)
