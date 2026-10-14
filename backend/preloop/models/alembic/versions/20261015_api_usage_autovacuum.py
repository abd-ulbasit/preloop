"""Vacuum api_usage often enough that the Spend reads stay index-only (#1583).

Revision ID: 20261015_api_usage_autovacuum
Revises: 20261014_execution_end_idx

The Spend headline (`/cost/summary`) and the gateway totals read the last 30
to 60 days of an account's usage through the covering partial index
`ix_api_usage_gateway_totals`. An index-only scan stays index-only only for
pages the visibility map marks all-visible, and only VACUUM sets those bits.
With the default `autovacuum_vacuum_insert_scale_factor` of 0.2, a table of
several million rows waits for hundreds of thousands of new rows before it is
vacuumed again. Those unvacuumed rows are the newest ones, which are exactly
the rows the Spend page reads. On a prod-shaped seed, 10% unvacuumed rows made
the planner give up on the index and scan the whole table: the headline call
went from 0.3 s to 0.5 s warm and to 3.7 s cold. A reprice, which rewrites
every row in its range, does the same until the next vacuum.

Per-table settings let autovacuum run after about 1% new or changed rows.
Vacuuming freshly appended pages is cheap.
`ALTER TABLE ... SET (...)` takes a SHARE UPDATE EXCLUSIVE lock, which does
not block reads or writes.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261015_api_usage_autovacuum"
down_revision: Union[str, None] = "20261014_execution_end_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

SETTINGS = {
    "autovacuum_vacuum_insert_scale_factor": "0.01",
    "autovacuum_vacuum_scale_factor": "0.02",
    "autovacuum_analyze_scale_factor": "0.02",
}


def upgrade() -> None:
    """Set the per-table autovacuum thresholds on api_usage."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    options = ", ".join(f"{name} = {value}" for name, value in SETTINGS.items())
    op.execute(f"ALTER TABLE api_usage SET ({options})")


def downgrade() -> None:
    """Return api_usage to the server-wide autovacuum thresholds."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f"ALTER TABLE api_usage RESET ({', '.join(SETTINGS)})")
