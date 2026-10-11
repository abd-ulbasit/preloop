"""Run a non-secret read-only identity migration audit.

Usage: ``python -m preloop.models.identity_audit`` after the identity backfill.
Use a database role with SELECT privileges for operational enforcement.
"""

import json

from preloop.models.crud import crud_person
from preloop.models.db.session import get_db_session


def main() -> None:
    """Print counts and actionable reasons without addresses or credentials."""
    sessions = get_db_session()
    db = next(sessions)
    try:
        print(json.dumps(crud_person.audit(db), indent=2))
    finally:
        sessions.close()


if __name__ == "__main__":
    main()
