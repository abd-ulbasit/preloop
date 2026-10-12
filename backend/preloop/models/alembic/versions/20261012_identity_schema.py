"""Add authoritative identity fields and credential ownership (expand stage).

Drain credential writers while applying this revision and its backfill. Existing
readers can read user profile/password caches until the auth cutover; subsequent
canonical password resets deliberately clear those caches. Downgrade is refused
once credentials can no longer be recovered from membership rows.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261012_identity_schema"
down_revision = "20261011_audit_outbox"
branch_labels = None
depends_on = None
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS


def upgrade() -> None:
    """Add nullable canonical values, then identity-owned credential records."""
    for name, type_ in (
        ("username", sa.String(255)),
        ("email", sa.String(255)),
        ("full_name", sa.String(255)),
        ("avatar_url", sa.Text()),
        ("avatar_source", sa.String(20)),
        ("hashed_password", sa.String(255)),
        ("migration_conflict", sa.Text()),
        ("migration_resolution", sa.Text()),
    ):
        op.add_column("person", sa.Column(name, type_, nullable=True))
    op.add_column(
        "person",
        sa.Column(
            "identity_status", sa.String(20), nullable=False, server_default="active"
        ),
    )
    op.add_column(
        "person",
        sa.Column("auth_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_person_username", "person", ["username"], unique=True)
    op.create_check_constraint(
        "ck_person_identity_status",
        "person",
        "identity_status IN ('active','suspended','quarantined')",
    )
    op.create_check_constraint(
        "ck_person_auth_generation", "person", "auth_generation >= 0"
    )
    for table, extras, constraint in (
        (
            "person_auth_identity",
            [
                sa.Column("source", sa.String(50), nullable=False),
                sa.Column("provider", sa.String(255), nullable=False),
                sa.Column("subject", sa.String(255), nullable=False),
            ],
            sa.UniqueConstraint(
                "source", "provider", "subject", name="uq_person_auth_subject"
            ),
        ),
        (
            "person_password_credential",
            [sa.Column("hashed_password", sa.String(255), nullable=False)],
            sa.UniqueConstraint(
                "person_id", "hashed_password", name="uq_person_password_hash"
            ),
        ),
    ):
        op.create_table(
            table,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "person_id",
                UUID(as_uuid=True),
                sa.ForeignKey("person.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "origin_user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("user.id", ondelete="SET NULL"),
            ),
            *extras,
            constraint,
        )
        op.create_index(f"ix_{table}_person_id", table, ["person_id"])
    op.add_column(
        "webauthn_credential", sa.Column("person_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_webauthn_person",
        "webauthn_credential",
        "person",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_webauthn_credential_person_id", "webauthn_credential", ["person_id"]
    )


def downgrade() -> None:
    """Refuse unsafe credential loss; rollback requires a deliberate forward repair."""
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM person WHERE hashed_password IS NOT NULL)
         OR EXISTS (SELECT 1 FROM person_auth_identity)
         OR EXISTS (SELECT 1 FROM person_password_credential) THEN
        RAISE EXCEPTION 'identity credentials exist: forward repair required';
      END IF;
    END $$""")
    op.drop_column("webauthn_credential", "person_id")
    op.drop_table("person_password_credential")
    op.drop_table("person_auth_identity")
    op.drop_constraint("ck_person_identity_status", "person")
    op.drop_constraint("ck_person_auth_generation", "person")
    for name in (
        "username",
        "email",
        "full_name",
        "avatar_url",
        "avatar_source",
        "hashed_password",
        "migration_conflict",
        "migration_resolution",
        "identity_status",
        "auth_generation",
    ):
        op.drop_column("person", name)
