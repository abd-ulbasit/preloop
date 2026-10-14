#!/bin/bash
# shellcheck disable=SC2034
# Preloop hosted Codex runtime selection.
#
# These defaults match backend/preloop/agents/codex.py. The universal image
# smoke sources this file so it exercises the toolchains a hosted run gets.
# Keep the names and values aligned with that file; the static test fails
# when they drift.
CODEX_ENV_PYTHON_VERSION=3.12
CODEX_ENV_NODE_VERSION=20
CODEX_ENV_RUST_VERSION=1.87.0
CODEX_ENV_GO_VERSION=1.23.8
CODEX_ENV_SWIFT_VERSION=6.2
CODEX_ENV_RUBY_VERSION=3.4.4
CODEX_ENV_PHP_VERSION=8.4
