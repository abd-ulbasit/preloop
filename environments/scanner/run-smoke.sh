#!/bin/bash
# Smoke a built scanner image with networking disabled (issue #705).
# Checks both manifests and that Trivy finds the fixture's known
# vulnerabilities from the baked database alone.
#
# Usage: environments/scanner/run-smoke.sh IMAGE
set -euo pipefail
image="${1:?usage: $0 IMAGE}"
run() { docker run --rm --network none --entrypoint "$1" "$image" "${@:2}"; }

echo "image: $image"
echo "image id: $(docker image inspect --format '{{.Id}}' "$image")"
echo "size bytes: $(docker image inspect --format '{{.Size}}' "$image")"
scanners="$(run cat /opt/preloop-scanners.json)"
echo "preloop-scanners.json: $scanners"
python3 - "$scanners" <<'PY'
import json, re, sys
t = json.loads(sys.argv[1])["trivy"]
assert re.fullmatch(r"\d+\.\d+\.\d+", t["version"]), t
assert re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", t["db_repository"]), t
assert t["db_updated_at"] and t["baked_at"], t
PY
environment="$(run cat /opt/preloop-environment.json)"
echo "preloop-environment.json: $environment"
python3 - "$environment" <<'PY'
import json, sys
p = json.loads(sys.argv[1])
assert p.get("version") == 1 and p.get("harness") == "codex", p
PY
report="$(run trivy --quiet fs --skip-db-update --skip-java-db-update \
    --scanners vuln --format json /opt/preloop-scanner/fixtures/vulnerable)"
count="$(python3 -c 'import json,sys; r=json.load(sys.stdin); print(sum(len(x.get("Vulnerabilities") or []) for x in r.get("Results") or []))' <<<"$report")"
echo "offline vulnerabilities found: $count"
if [ "$count" -lt 1 ]; then
    echo "FAIL: no findings with --network none" >&2
    exit 1
fi
echo "OK"
