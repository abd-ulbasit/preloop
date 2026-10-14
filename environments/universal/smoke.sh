#!/bin/bash
# Offline build gate for the universal Codex image.
#
# Runs through /opt/entrypoint.sh so the Codex setup selects the Preloop
# CODEX_ENV_* runtimes first. Prints one "PASS <language>" line for Python,
# Java, Go, Rust, TypeScript, Perl, and Shell. A missing tool fails with
# "missing required tool: <name>" before any PASS line.
#
# Perl reuses environments/perl/perl-toolchain-smoke.sh, copied next to this
# script in the image, and also runs perlver --blame on the compatible fixture.
set -euo pipefail

root="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
# shellcheck disable=SC1091
source "$root/tools.sh"

fail() {
    echo "universal-smoke: FAIL: $*" >&2
    exit 1
}

for tool in "${UNIVERSAL_TOOLS[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || fail "missing required tool: $tool"
done

if [ -z "${CODEX_BASE_IMAGE:-}" ]; then
    fail "CODEX_BASE_IMAGE is unset; the manifest must name the digest-pinned base"
fi
python3 "$root/write_manifest.py" \
    --output /opt/preloop-environment.json \
    --base-image "$CODEX_BASE_IMAGE"
chmod a+r /opt/preloop-environment.json

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python_dir="$work/python"
mkdir -p "$python_dir"
cp "$root/fixtures/python/test_fixture.py" "$python_dir/"
(
    cd "$python_dir"
    mypy test_fixture.py
    ruff check test_fixture.py
    pytest -q --noconftest test_fixture.py
)
echo "PASS python"

if [ ! -d "$root/m2" ]; then
    fail "maven local repo is missing: $root/m2"
fi
java_dir="$work/java"
mkdir -p "$java_dir/classes"
cp -a "$root/fixtures/java/." "$java_dir/"
javac -d "$java_dir/classes" "$java_dir/src/main/java/demo/Hello.java"
mvn -o -q package -f "$java_dir/pom.xml" -Dmaven.repo.local="$root/m2"
echo "PASS java"

go_dir="$work/go"
cp -a "$root/fixtures/go/." "$go_dir/"
(
    cd "$go_dir"
    GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GO111MODULE=on go vet .
    GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GO111MODULE=on golangci-lint run ./...
)
echo "PASS go"

rust_dir="$work/rust"
cp -a "$root/fixtures/rust/." "$rust_dir/"
(
    cd "$rust_dir"
    CARGO_NET_OFFLINE=true cargo check --offline
    CARGO_NET_OFFLINE=true cargo clippy --offline
)
echo "PASS rust"

ts_dir="$work/typescript"
cp -a "$root/fixtures/typescript/." "$ts_dir/"
(
    cd "$ts_dir"
    tsc --noEmit -p tsconfig.json
    eslint hello.js
)
echo "PASS typescript"

perl_smoke="$root/perl-toolchain-smoke.sh"
perl_fixtures="$root/perl-fixtures"
[ -f "$perl_smoke" ] || fail "missing fixture: $perl_smoke"
bash "$perl_smoke" "$perl_fixtures"
blame_file="$perl_fixtures/compatible-5_10.pl"
if ! blame="$(perlver --blame "$blame_file" 2>&1)"; then
    printf '%s\n' "$blame" >&2
    fail "perlver --blame failed"
fi
printf '%s\n' "$blame"
if ! grep -F 'compatible-5_10.pl' <<<"$blame" >/dev/null; then
    fail "perlver --blame did not name the Perl fixture"
fi
if ! grep -E 'Version[[:space:]]*:[[:space:]]*v?[0-9]+\.[0-9]+' <<<"$blame" >/dev/null; then
    fail "perlver --blame did not print a minimum version"
fi
echo "PASS perl"

shellcheck "$root/fixtures/shell/ok.sh" || fail "shellcheck rejected ok.sh"
if shellcheck "$root/fixtures/shell/bad.sh"; then
    fail "shellcheck accepted the deliberately broken script"
fi
echo "PASS shell"

echo "universal-smoke: OK"
