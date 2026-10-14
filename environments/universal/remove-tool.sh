#!/bin/bash
# Remove one toolchain command so the universal smoke must fail.
#
# pyenv recreates a shim on the next login when the real binary is still
# installed, so deleting `which` output is not enough for mypy, ruff, or
# pytest. This also removes the versioned binaries under PYENV_ROOT.
set -euo pipefail

name="${1:?tool name required}"
pyenv_root="${PYENV_ROOT:-/root/.pyenv}"
paths=()

if command -v pyenv >/dev/null 2>&1; then
    real="$(pyenv which "$name" 2>/dev/null || true)"
    if [ -n "$real" ]; then
        paths+=("$real")
    fi
fi

while IFS= read -r path; do
    if [ -n "$path" ]; then
        paths+=("$path")
    fi
done < <(which -a "$name" 2>/dev/null || true)

if [ -d "$pyenv_root" ]; then
    while IFS= read -r path; do
        if [ -n "$path" ]; then
            paths+=("$path")
        fi
    done < <(
        find "$pyenv_root/versions" "$pyenv_root/shims" \
            -name "$name" \( -type f -o -type l \) 2>/dev/null || true
    )
fi

if [ "${#paths[@]}" -eq 0 ]; then
    echo "did not find ${name}" >&2
    exit 1
fi

rm -f "${paths[@]}"
hash -r
if command -v "$name" >/dev/null 2>&1; then
    echo "still present: ${name}" >&2
    exit 1
fi
