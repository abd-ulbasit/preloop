# Opt-in scanner image for Codex

`Dockerfile` here builds a Codex-compatible agent image that adds Trivy at a
pinned version (checksum-verified release tarball) and bakes its advisory
database, fetched by OCI digest rather than by tag. It extends the default
Codex agent image pinned by digest. It is never a default: nothing changes
until an operator registers it as an environment profile.

Build locally:

    environments/scanner/resolve-db-digest.sh   # prints ghcr.io/aquasecurity/trivy-db@sha256:...
    docker build -f environments/scanner/Dockerfile \
      --build-arg TRIVY_DB_REPOSITORY="$(environments/scanner/resolve-db-digest.sh)" \
      -t preloop-codex-scanner:local .
    environments/scanner/run-smoke.sh preloop-codex-scanner:local

The build refuses a base or database reference that is not `name@sha256:...`.
Given the same build arguments the image contents are the same except
`baked_at` in `/opt/preloop-scanners.json`.

Files written into the image:

- `/opt/preloop-scanners.json`: Trivy version, `db_repository` with digest,
  the database `UpdatedAt` (`db_updated_at`) and `baked_at`.
- `/opt/preloop-environment.json`: `{"version": 1, "harness": "codex"}`, the
  environment profile setup protocol marker.
- `/opt/trivy-cache`: the baked database; `TRIVY_CACHE_DIR` points at it.

Scan offline with `trivy --skip-db-update --skip-java-db-update fs --scanners vuln <path>`.
The Java database is not baked.

`.github/workflows/publish-scanner-image.yml` publishes the image to
`ghcr.io/preloop/preloop-codex-scanner` on version tags only and prints the
published digest. Scheduled and manual runs build and smoke without pushing.
Registering the digest: see `docs/guide/flows/scanner-image.md` and
`profile.example.json`.
