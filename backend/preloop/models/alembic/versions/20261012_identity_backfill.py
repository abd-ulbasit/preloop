"""Backfill explicit Person links, preserving identity credentials and provenance.

No email-based merge and no username suffix inference. Valid primary pointers
select supplied canonical names; stale pointers fall back to most recent login,
then creation time and ID. Conflicting addresses/provider claims are quarantined,
with non-secret reasons. Restart-safe: completed persons and existing links are
left intact. This stage retains profile/password caches for a coordinated reader
cutover, but new password changes must use canonical CRUD rather than old writers.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, insert

revision = "20261012_identity_backfill"
down_revision = "20261012_identity_schema"
branch_labels = None
depends_on = None
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS


def _normalize(email: str) -> str:
    return email.strip(" \t\n\r\f\v").lower()


def upgrade() -> None:
    """Select proven profile values; copy credentials to one explicit owner."""
    bind = op.get_bind()
    # Credential writers must be drained. Locks prevent old writers/deletion
    # from racing the credential snapshot and retention-trigger replacement.
    op.execute(
        'LOCK TABLE person, "user", webauthn_credential IN SHARE ROW EXCLUSIVE MODE'
    )
    users: dict[Any, list[Any]] = defaultdict(list)
    for row in bind.execute(
        sa.text(
            'SELECT * FROM "user" ORDER BY last_login DESC NULLS LAST, created_at DESC, id'
        )
    ).mappings():
        users[row["person_id"]].append(row)
    providers: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for rows in users.values():
        for row in rows:
            source = row["user_source"]
            if source == "oauth" or source.startswith("oauth_"):
                provider = row["oauth_provider"] or (
                    source[6:] if source.startswith("oauth_") else None
                )
                subject = row["oauth_id"] or row["external_id"]
                if provider and subject:
                    providers[(f"oauth_{provider}", provider, subject)].append(row)
            elif source not in {"local", "oauth"} and row["external_id"]:
                # Older schema did not record issuer. Account namespace keeps
                # unrelated LDAP/SAML deployments from claiming each other's IDs.
                providers[
                    (source, f"legacy-account:{row['account_id']}", row["external_id"])
                ].append(row)
    conflicting_people = {
        r["person_id"]
        for rows in providers.values()
        if len({r["person_id"] for r in rows}) > 1
        for r in rows
    }
    passkey_owners: dict[Any, set[Any]] = defaultdict(set)
    for credential in bind.execute(
        sa.text("SELECT user_id FROM webauthn_credential WHERE user_id IS NOT NULL")
    ).mappings():
        passkey_owners[credential["user_id"]].add(credential["user_id"])
    provider_keys = {r["id"]: key for key, rows in providers.items() for r in rows}
    people = list(bind.execute(sa.text("SELECT * FROM person ORDER BY id")).mappings())
    claimed_names = {
        person["username"]: person["id"]
        for person in people
        if person["username"] is not None
    }
    profile_updates: list[dict[str, Any]] = []
    quarantines: list[dict[str, Any]] = []
    passwords: list[dict[str, Any]] = []
    for person in people:
        rows = users.get(person["id"], [])
        if person["username"] is not None or person["migration_conflict"] is not None:
            continue
        reasons = []
        if not rows:
            reasons.append("no_membership_profile")
        if rows and len({_normalize(r["email"]) for r in rows}) != 1:
            reasons.append("linked_membership_email_conflict")
        if rows and _normalize(rows[0]["email"]) != person["email_normalized"]:
            reasons.append("person_membership_email_conflict")
        if person["id"] in conflicting_people:
            reasons.append("provider_subject_claimed_by_multiple_people")
        primary = next(
            (r for r in rows if r["id"] == person["primary_user_id"]),
            rows[0] if rows else None,
        )
        if primary is not None:
            existing_name_owner = claimed_names.get(primary["username"])
            if existing_name_owner is not None and existing_name_owner != person["id"]:
                reasons.append("canonical_username_claimed_by_another_person")
            if any(
                r["id"] != primary["id"]
                and r["hashed_password"]
                and r["hashed_password"] != primary["hashed_password"]
                for r in rows
            ):
                reasons.append("nonprimary_password_ownership_conflict")
            if any(
                r["id"] != primary["id"] and r["id"] in passkey_owners for r in rows
            ):
                reasons.append("nonprimary_passkey_ownership_conflict")
            primary_provider = provider_keys.get(primary["id"])
            if any(
                r["id"] != primary["id"]
                and r["id"] in provider_keys
                and provider_keys[r["id"]] != primary_provider
                for r in rows
            ):
                reasons.append("nonprimary_provider_ownership_conflict")
        if reasons:
            quarantines.append(
                {"id": person["id"], "reason": ";".join(reasons)},
            )
        elif primary is not None:
            claimed_names[primary["username"]] = person["id"]
            profile_updates.append(
                {
                    key: primary[key]
                    for key in (
                        "username",
                        "email",
                        "full_name",
                        "avatar_url",
                        "avatar_source",
                        "hashed_password",
                        "auth_generation",
                    )
                }
                | {"id": person["id"], "primary_user_id": primary["id"]},
            )
        for row in rows:
            if row["hashed_password"] and (
                primary is None
                or row["hashed_password"] != primary["hashed_password"]
                or reasons
            ):
                passwords.append(
                    {
                        "id": uuid.uuid4(),
                        "person_id": person["id"],
                        "origin_user_id": row["id"],
                        "hashed_password": row["hashed_password"],
                    },
                )
    provider_links: list[dict[str, Any]] = []
    for (source, provider, subject), rows in providers.items():
        if len({r["person_id"] for r in rows}) != 1:
            continue
        origin = rows[0]
        provider_links.append(
            {
                "id": uuid.uuid4(),
                "person_id": origin["person_id"],
                "origin_user_id": origin["id"],
                "source": source,
                "provider": provider,
                "subject": subject,
            },
        )
    # Batch profiles and use explicit multi-values credential inserts. Plain
    # psycopg executemany can still issue one server round trip per inserted row.
    for statement, records in (
        (
            "UPDATE person SET identity_status='quarantined', migration_conflict=:reason WHERE id=:id",
            quarantines,
        ),
        (
            """UPDATE person SET username=:username,email=:email,full_name=:full_name,
            avatar_url=:avatar_url,avatar_source=:avatar_source,hashed_password=:hashed_password,
            auth_generation=:auth_generation,primary_user_id=:primary_user_id
            WHERE id=:id AND username IS NULL""",
            profile_updates,
        ),
    ):
        for start in range(0, len(records), 1000):
            bind.execute(sa.text(statement), records[start : start + 1000])
    for table_name, fields, conflict_columns, records in (
        (
            "person_password_credential",
            ["hashed_password"],
            ["person_id", "hashed_password"],
            passwords,
        ),
        (
            "person_auth_identity",
            ["source", "provider", "subject"],
            ["source", "provider", "subject"],
            provider_links,
        ),
    ):
        table = sa.table(
            table_name,
            *(
                sa.column(name, UUID(as_uuid=True))
                for name in ("id", "person_id", "origin_user_id")
            ),
            *(sa.column(name, sa.String()) for name in fields),
        )
        for start in range(0, len(records), 1000):
            bind.execute(
                insert(table)
                .values(records[start : start + 1000])
                .on_conflict_do_nothing(index_elements=conflict_columns)
            )
    op.execute(
        'UPDATE webauthn_credential c SET person_id=u.person_id FROM "user" u WHERE c.user_id=u.id AND c.person_id IS NULL'
    )
    op.alter_column("webauthn_credential", "person_id", nullable=False)
    # Replace the membership cascade with provenance retention.
    for fk in sa.inspect(bind).get_foreign_keys("webauthn_credential"):
        if fk["constrained_columns"] == ["user_id"]:
            op.drop_constraint(fk["name"], "webauthn_credential", type_="foreignkey")
    op.alter_column("webauthn_credential", "user_id", nullable=True)
    op.create_foreign_key(
        "fk_webauthn_origin_user",
        "webauthn_credential",
        "user",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("""CREATE OR REPLACE FUNCTION preloop_user_deleted() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      DELETE FROM account_access_grant WHERE subject_type='user' AND subject_id=OLD.id;
      -- Person and credentials are retained after the last membership disappears.
      RETURN NULL;
    END $$""")


def downgrade() -> None:
    """Credential ownership cannot safely be moved back into membership cascades."""
    raise RuntimeError("Identity backfill is irreversible; use a forward repair")
