"""Run a non-secret read-only identity migration audit.

Usage: ``python -m preloop.models.identity_audit`` after the identity backfill.
Use a database role with SELECT privileges for operational enforcement.
"""

import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from preloop.models.crud import crud_person


def main() -> None:
    """Print counts and actionable reasons without addresses or credentials."""
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with Session(engine, autoflush=False) as db:
            print(json.dumps(crud_person.audit(db, read_only=True), indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
