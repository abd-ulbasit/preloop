#!/bin/bash
# Resolve the current Trivy advisory database tag to an OCI digest reference.
# The build only ever consumes the printed name@sha256:... form.
#
# Usage: environments/scanner/resolve-db-digest.sh [REPOSITORY:TAG]
set -euo pipefail
ref="${1:-ghcr.io/aquasecurity/trivy-db:2}"
repo="${ref%:*}"
digest="$(docker buildx imagetools inspect --format '{{json .Manifest}}' "$ref" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["digest"])')"
case "$digest" in
    sha256:*) ;;
    *) echo "could not resolve a digest for $ref" >&2; exit 1 ;;
esac
echo "${repo}@${digest}"
