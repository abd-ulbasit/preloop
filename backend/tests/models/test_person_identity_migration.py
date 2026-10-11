"""Run both identity stages against actual legacy PostgreSQL tables."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.orm import Session


VERSIONS = Path(__file__).resolve().parents[2] / "preloop/models/alembic/versions"


def _revision(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_backfill_conflicts_provisional_primary_and_restart(
    db_session: Session,
) -> None:
    connection = db_session.connection()
    schema = "identity_test_" + uuid.uuid4().hex
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    for ddl in (
        """CREATE TABLE person (id UUID PRIMARY KEY, created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now(), email_normalized VARCHAR(255) NOT NULL,
            email_verified_at TIMESTAMPTZ, primary_user_id UUID, last_active_user_id UUID)""",
        """CREATE TABLE "user" (id UUID PRIMARY KEY, person_id UUID REFERENCES person(id),
            account_id UUID NOT NULL, username VARCHAR(255) UNIQUE NOT NULL,
            email VARCHAR(255) NOT NULL, email_verified BOOLEAN, full_name VARCHAR(255),
            avatar_url TEXT, avatar_source VARCHAR(20), hashed_password VARCHAR(255),
            auth_generation INTEGER DEFAULT 0, user_source VARCHAR(50), oauth_provider VARCHAR(50),
            oauth_id VARCHAR(255), external_id VARCHAR(255), last_login TIMESTAMPTZ,
            created_at TIMESTAMP DEFAULT now(), UNIQUE(person_id,account_id))""",
        """CREATE TABLE webauthn_credential (id UUID PRIMARY KEY, credential_id TEXT UNIQUE,
            public_key TEXT, sign_count BIGINT, user_id UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE)""",
        "CREATE TABLE account_access_grant (subject_type TEXT,subject_id UUID)",
    ):
        connection.execute(text(ddl))
    people = [uuid.uuid4() for _ in range(6)]
    memberships = [uuid.uuid4() for _ in range(7)]
    for index, person_id in enumerate(people):
        connection.execute(
            text(
                "INSERT INTO person(id,email_normalized,email_verified_at,primary_user_id) VALUES (:id,:email,CASE WHEN :verified THEN now() END,:primary)"
            ),
            {
                "id": person_id,
                "email": f"p{index}@example.com",
                "verified": index == 0,
                "primary": memberships[0] if index == 0 else uuid.uuid4(),
            },
        )
    # Verified linked duplicates, explicit provisional links, an unrelated equal-email
    # registration and two separate people claiming one OAuth subject.
    layout = [
        (0, "chosen-name", "p0@example.com", "hash-a", None),
        (0, "membership-alias", "p0@example.com", "hash-a", None),
        (1, "provisional-chosen", "p1@example.com", "hash-c", None),
        (1, "provisional-alias", "p1@example.com", None, None),
        (2, "unrelated-provisional", "p1@example.com", "hash-d", None),
        (3, "provider-one", "p3@example.com", None, "subject-1"),
        (4, "provider-two", "p4@example.com", None, "subject-1"),
    ]
    # Align only the unrelated provisional's own address, without linking persons.
    connection.execute(
        text("UPDATE person SET email_normalized='p1@example.com' WHERE id=:id"),
        {"id": people[2]},
    )
    for user_id, (index, name, email, password, subject) in zip(
        memberships, layout, strict=True
    ):
        connection.execute(
            text("""INSERT INTO "user" (id,person_id,account_id,username,email,email_verified,
            hashed_password,user_source,oauth_provider,oauth_id) VALUES (:id,:person,:account,:name,:email,:verified,:password,:source,:provider,:subject)"""),
            {
                "id": user_id,
                "person": people[index],
                "account": uuid.uuid4(),
                "name": name,
                "email": email,
                "verified": index == 0,
                "password": password,
                "source": "oauth" if subject else "local",
                "provider": "example-provider" if subject else None,
                "subject": subject,
            },
        )
    connection.execute(
        text('UPDATE "user" SET last_login=now() WHERE id=:id'), {"id": memberships[2]}
    )
    credential_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO webauthn_credential(id,user_id,credential_id,public_key,sign_count) VALUES (:id,:user,'unchanged-passkey','unchanged-key',42)"
        ),
        {"id": credential_id, "user": memberships[0]},
    )
    with Operations.context(MigrationContext.configure(connection)):
        _revision("20261012_identity_schema").upgrade()
        _revision("20261012_identity_backfill").upgrade()
        _revision("20261012_identity_backfill").upgrade()
    persons = {
        row["id"]: row
        for row in connection.execute(text("SELECT * FROM person")).mappings()
    }
    assert persons[people[0]]["username"] == "chosen-name"
    assert persons[people[0]]["hashed_password"] == "hash-a"
    assert persons[people[1]]["username"] == "provisional-chosen"
    assert persons[people[1]]["email_verified_at"] is None
    assert persons[people[2]]["id"] != persons[people[1]]["id"]
    assert (
        persons[people[3]]["migration_conflict"]
        == "provider_subject_claimed_by_multiple_people"
    )
    assert persons[people[4]]["identity_status"] == "quarantined"
    assert persons[people[5]]["migration_conflict"] == "no_membership_profile"
    hashes = set(
        connection.execute(
            text(
                "SELECT hashed_password FROM person_password_credential WHERE person_id=:id"
            ),
            {"id": people[0]},
        ).scalars()
    )
    assert hashes == set()
    assert (
        connection.execute(text("SELECT count(*) FROM person_auth_identity")).scalar()
        == 0
    )
    passkey = (
        connection.execute(
            text("SELECT * FROM webauthn_credential WHERE id=:id"),
            {"id": credential_id},
        )
        .mappings()
        .one()
    )
    assert passkey["credential_id"] == "unchanged-passkey"
    assert passkey["public_key"] == "unchanged-key"
    assert passkey["sign_count"] == 42
    assert passkey["person_id"] == people[0]
    connection.execute(text('DELETE FROM "user" WHERE id=:id'), {"id": memberships[0]})
    retained = (
        connection.execute(
            text("SELECT * FROM webauthn_credential WHERE id=:id"),
            {"id": credential_id},
        )
        .mappings()
        .one()
    )
    assert retained["user_id"] is None
    assert retained["person_id"] == people[0]
    connection.execute(text("SET LOCAL search_path TO public"))


def test_competing_membership_credentials_are_preserved_but_quarantined(
    db_session: Session,
) -> None:
    from preloop.models import models
    from preloop.models.crud import crud_person
    from preloop.models.crud.person import IdentityUnavailableError
    import pytest

    accounts = [models.Account(organization_name="Example") for _ in range(2)]
    db_session.add_all(accounts)
    db_session.flush()
    source = models.User(
        account_id=accounts[0].id,
        username=uuid.uuid4().hex,
        email="legacy@example.com",
        hashed_password="primary-hash",
    )
    db_session.add(source)
    db_session.flush()
    sibling = models.User(
        account_id=accounts[1].id,
        person=source.person,
        username=uuid.uuid4().hex,
        email=source.email,
    )
    db_session.add(sibling)
    db_session.flush()
    # Represent legacy data created before canonical credentials existed.
    sibling.hashed_password = "unproven-membership-hash"
    source.person.username = None
    source.person.email = None
    source.person.hashed_password = None
    db_session.flush()
    connection = db_session.connection()
    with Operations.context(MigrationContext.configure(connection)):
        _revision("20261012_identity_backfill").upgrade()
    db_session.expire_all()
    person = db_session.get(models.Person, source.person_id)
    assert person.identity_status == "quarantined"
    assert person.migration_conflict == "nonprimary_password_ownership_conflict"
    with pytest.raises(IdentityUnavailableError):
        crud_person.credential_owner(db_session, membership_id=source.id)
    with pytest.raises(IdentityUnavailableError):
        crud_person.list_password_hashes(db_session, person_id=person.id)
    preserved = set(
        connection.execute(
            text(
                "SELECT hashed_password FROM person_password_credential WHERE person_id=:id"
            ),
            {"id": person.id},
        ).scalars()
    )
    assert preserved == {"primary-hash", "unproven-membership-hash"}


def test_canonical_username_conflict_is_quarantined_without_aborting(
    db_session: Session,
) -> None:
    from preloop.models import models

    accounts = [models.Account(organization_name="Example") for _ in range(2)]
    db_session.add_all(accounts)
    db_session.flush()
    source = models.User(
        account_id=accounts[0].id,
        username=uuid.uuid4().hex,
        email="source@example.com",
        hashed_password="source-hash",
    )
    existing = models.User(
        account_id=accounts[1].id,
        username=uuid.uuid4().hex,
        email="existing@example.com",
    )
    db_session.add_all([source, existing])
    db_session.flush()
    supplied_username = source.username
    source.person.username = None
    source.person.email = None
    source.person.hashed_password = None
    db_session.flush()
    existing.person.username = supplied_username
    db_session.flush()
    with Operations.context(MigrationContext.configure(db_session.connection())):
        _revision("20261012_identity_backfill").upgrade()
    db_session.expire_all()
    assert source.person.identity_status == "quarantined"
    assert (
        source.person.migration_conflict
        == "canonical_username_claimed_by_another_person"
    )
    assert existing.person.username == supplied_username
