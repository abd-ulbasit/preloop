#!/bin/sh
# Offline regression checks. Pass a source-build CLI, never the user's installed CLI.
set -eu
export PRELOOP_DISABLE_TELEMETRY=true
cli=${1:?usage: test_mdm_recipes.sh /absolute/path/to/test-cli}
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
mkdir -p "$tmp/out"
for platform in darwin linux windows; do
    target=/opt/preloop/bin/preloop
    if [ "$platform" = windows ]; then target='C:\Program Files\Preloop\preloop.exe'; fi
    sh "$root/docs/assets/mdm-recipes/export-overlay.sh" "$cli" "$tmp/out/$platform" "$platform" "$target"
done
python3 - "$tmp/out" <<'PY'
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
for platform in ('darwin', 'linux', 'windows'):
    bundle = root / platform
    manifest = json.loads((bundle / 'manifest.json').read_text())
    settings = json.loads((bundle / 'managed-settings.json').read_text())
    assert manifest['verification'] == 'unverified'
    assert manifest['platform'] == platform
    assert manifest['keys'] == ['hooks.PreToolUse']
    assert set(settings) == {'hooks'}
    assert set(settings['hooks']) == {'PreToolUse'}
    assert 'permission-hook' in json.dumps(settings)
    assert len(list(bundle.iterdir())) == 3
PY
# Existing bundles must be refused without overwrite and remain byte-identical.
cp "$tmp/out/darwin/managed-settings.json" "$tmp/original.json"
if sh "$root/docs/assets/mdm-recipes/export-overlay.sh" "$cli" "$tmp/out/darwin" darwin /opt/preloop/bin/preloop >/dev/null 2>&1; then
    echo 'Existing artifact was unexpectedly overwritten' >&2
    exit 1
fi
cmp "$tmp/original.json" "$tmp/out/darwin/managed-settings.json"
# Check command flags against the binary without scanning host profiles or secrets.
"$cli" agents discover --help > "$tmp/help"
python3 - "$tmp/help" <<'PY'
import pathlib
import sys
text = pathlib.Path(sys.argv[1]).read_text()
for flag in ('--inventory', '--all-users', '--report', '--device-ref'):
    assert flag in text, flag
PY
# Simulate a root scheduler; the user template must refuse before invoking any CLI.
mkdir "$tmp/bin"
printf '#!/bin/sh\nprintf "0\\n"\n' > "$tmp/bin/id"
chmod +x "$tmp/bin/id"
if PATH="$tmp/bin:$PATH" sh "$root/docs/assets/mdm-recipes/inventory-user.sh" > "$tmp/refusal" 2>&1; then
    echo 'Root context was unexpectedly accepted' >&2
    exit 1
fi
for script in "$root"/docs/assets/mdm-recipes/*.sh; do sh -n "$script"; done
# A synthetic non-root session must invoke only the offline collector arguments.
printf '#!/bin/sh\nprintf "1000\\n"\n' > "$tmp/bin/id"
cat > "$tmp/bin/fixture-cli" <<'CLI'
#!/bin/sh
[ "$PRELOOP_DISABLE_TELEMETRY" = true ] || exit 1
[ "$#" -eq 3 ] || exit 1
[ "$1" = agents ] && [ "$2" = discover ] && [ "$3" = --inventory ] || exit 1
printf '{"schema":"synthetic-offline-fixture"}\n'
CLI
chmod +x "$tmp/bin/fixture-cli"
PATH="$tmp/bin:$PATH" sh "$root/docs/assets/mdm-recipes/inventory-user.sh" "$tmp/bin/fixture-cli" > "$tmp/user.json"
python3 - "$root/docs/assets/mdm-recipes" "$tmp/user.json" <<'PYTEST'
import json
import pathlib
import sys
import yaml
assets = pathlib.Path(sys.argv[1])
assert json.loads(pathlib.Path(sys.argv[2]).read_text())['schema'] == 'synthetic-offline-fixture'
fragment = yaml.safe_load((assets / 'fleet-controls.yaml').read_text())
for script in fragment['controls']['scripts']:
    assert (assets / script['path']).is_file()
PYTEST
echo 'MDM fixture checks passed (no vendor tenant or live app validation).'
