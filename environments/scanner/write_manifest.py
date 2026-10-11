"""Write /opt/preloop-scanners.json from the baked Trivy cache (issue #705).

Run at image build time. It records the Trivy version, the database
repository with the digest the build resolved, the database UpdatedAt from
the downloaded metadata, and the build time. It refuses a tag reference.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

DIGEST_REF = re.compile(r"^[^\s@]+@sha256:[a-f0-9]{64}$")


def build_manifest(
    *, version: str, db_repository: str, cache_dir: Path, baked_at: str
) -> dict[str, dict[str, str]]:
    """Return the manifest, or raise ValueError when an input is not pinned."""
    if not DIGEST_REF.match(db_repository):
        raise ValueError(f"db repository must be pinned by digest: {db_repository!r}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"trivy version must be X.Y.Z: {version!r}")
    metadata = json.loads((cache_dir / "db" / "metadata.json").read_text())
    updated_at = metadata.get("UpdatedAt")
    if not isinstance(updated_at, str) or not updated_at:
        raise ValueError("trivy db metadata has no UpdatedAt")
    return {
        "trivy": {
            "version": version,
            "db_repository": db_repository,
            "db_updated_at": updated_at,
            "baked_at": baked_at,
        }
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--db-repository", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baked-at")
    args = parser.parse_args(argv)
    baked_at = args.baked_at or datetime.datetime.now(datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        manifest = build_manifest(
            version=args.version,
            db_repository=args.db_repository,
            cache_dir=args.cache_dir,
            baked_at=baked_at,
        )
    except (OSError, ValueError) as exc:
        print(f"preloop-scanners: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
