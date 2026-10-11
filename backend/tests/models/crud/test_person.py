"""Identity ownership survives membership deletion and rejects unproven merges."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from preloop.models import models
from preloop.models.crud import crud_person
from preloop.models.crud.person import IdentityConflictError, IdentityUnavailableError


def _member(db, *, username=None, email="jane@example.com", person=None):
    account = models.Account(organization_name="Example organization")
    db.add(account)
    db.flush()
    user = models.User(
        account_id=account.id,
        username=username or uuid.uuid4().hex,
        email=email,
        full_name="Jane Doe",
        hashed_password=None if person is not None else "synthetic-hash",
        email_verified=False,
        person=person,
    )
    db.add(user)
    db.flush()
    return user


def test_linked_provisional_memberships_share_canonical_profile(db_session):
    first = _member(db_session, username="chosen-" + uuid.uuid4().hex)
    second = _member(db_session, person=first.person)
    profile = crud_person.profile_for_membership(db_session, membership_id=second.id)
    assert profile.username == first.username
    assert not profile.email_verified
    assert (
        crud_person.credential_owner(db_session, membership_id=first.id).id
        == first.person_id
    )
    original_ids = (first.id, second.id)
    crud_person.update_profile(
        db_session,
        membership_id=second.id,
        changes={"full_name": "Jane Example"},
        commit=False,
    )
    assert (
        crud_person.profile_for_membership(db_session, membership_id=first.id).full_name
        == "Jane Example"
    )
    assert (first.id, second.id) == original_ids
    assert second.account_id != first.account_id


def test_equal_provisional_email_never_merges(db_session):
    first = _member(db_session)
    second = _member(db_session)
    assert first.person_id != second.person_id
    with pytest.raises(IdentityConflictError):
        crud_person.resolve_login(db_session, identifier="jane@example.com")


def test_security_changes_revoke_each_membership_and_retire_passwords(db_session):
    first = _member(db_session)
    second = _member(db_session, person=first.person)
    db_session.add(
        models.PersonPasswordCredential(
            person_id=first.person_id,
            hashed_password="old-hash",
            origin_user_id=second.id,
        )
    )
    db_session.flush()
    assert set(
        crud_person.list_password_hashes(db_session, person_id=first.person_id)
    ) == {"old-hash", "synthetic-hash"}
    crud_person.set_password(
        db_session,
        membership_id=second.id,
        hashed_password="new-hash",
        expected_generation=0,
        commit=False,
    )
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.auth_generation == second.auth_generation == 1
    assert first.hashed_password is None and second.hashed_password is None
    assert crud_person.list_password_hashes(db_session, person_id=first.person_id) == [
        "new-hash"
    ]
    with pytest.raises(IdentityConflictError):
        crud_person.set_password(
            db_session,
            person_id=first.person_id,
            hashed_password="replay-hash",
            expected_generation=0,
            commit=False,
        )


def test_delete_primary_and_last_membership_retains_credentials(db_session):
    first = _member(db_session)
    second = _member(db_session, person=first.person)
    person_id = first.person_id
    passkey = models.WebAuthnCredential(
        user_id=first.id,
        credential_id=uuid.uuid4().hex,
        public_key="synthetic-public-key",
    )
    db_session.add(passkey)
    db_session.flush()
    passkey_id = passkey.id
    # Raw deletion exercises the actual account/DB cascade rather than an ORM substitute.
    db_session.execute(delete(models.User).where(models.User.id == first.id))
    db_session.expire_all()
    assert (
        crud_person.credential_owner(db_session, membership_id=second.id).id
        == person_id
    )
    assert db_session.get(models.WebAuthnCredential, passkey_id).user_id is None
    db_session.execute(delete(models.User).where(models.User.id == second.id))
    db_session.expire_all()
    assert db_session.get(models.Person, person_id).hashed_password == "synthetic-hash"
    assert db_session.get(models.WebAuthnCredential, passkey_id).person_id == person_id
    crud_person.set_password(
        db_session, person_id=person_id, hashed_password="retained-reset", commit=False
    )
    crud_person.delete_identity(db_session, person_id=person_id, commit=False)
    db_session.expire_all()
    assert db_session.get(models.WebAuthnCredential, passkey_id) is None


def test_verification_address_bound_single_use_and_unrelated_conflict(db_session):
    first = _member(db_session)
    second = _member(db_session)
    crud_person.verify_email(
        db_session,
        person_id=first.person_id,
        expected_email="Jane@Example.com",
        expected_generation=0,
        commit=False,
    )
    with pytest.raises(IdentityConflictError):
        crud_person.verify_email(
            db_session,
            person_id=first.person_id,
            expected_email=first.email,
            expected_generation=0,
            commit=False,
        )
    with pytest.raises(IdentityConflictError):
        crud_person.verify_email(
            db_session,
            person_id=second.person_id,
            expected_email=second.email,
            expected_generation=0,
            commit=False,
        )


def test_username_conflicts_and_email_change_verification(db_session):
    first = _member(db_session)
    second = _member(db_session, email="other@example.com")
    with pytest.raises(IdentityConflictError):
        crud_person.update_profile(
            db_session,
            membership_id=first.id,
            changes={"username": second.username},
            commit=False,
        )
    crud_person.verify_email(
        db_session,
        person_id=first.person_id,
        expected_email=first.email,
        expected_generation=0,
        commit=False,
    )
    crud_person.update_profile(
        db_session,
        membership_id=first.id,
        changes={"email": "changed@example.com"},
        commit=False,
    )
    assert first.person.email_verified_at is None
    assert first.person.auth_generation == 2
    assert (
        crud_person.list_by_email(db_session, email="changed@example.com")[0].id
        == first.person_id
    )


def test_provider_ownership_and_quarantine_fail_closed(db_session):
    first = _member(db_session)
    second = _member(db_session)
    crud_person.link_provider(
        db_session,
        person_id=first.person_id,
        source="oauth",
        provider="https://issuer.example.com",
        subject="subject-1",
        commit=False,
    )
    with pytest.raises(IdentityConflictError):
        crud_person.link_provider(
            db_session,
            person_id=second.person_id,
            source="oauth",
            provider="https://issuer.example.com",
            subject="subject-1",
            commit=False,
        )
    first.person.identity_status = "quarantined"
    first.person.migration_conflict = "synthetic_conflict"
    db_session.flush()
    with pytest.raises(IdentityUnavailableError):
        crud_person.credential_owner(db_session, membership_id=first.id)
    report = crud_person.audit(db_session)
    assert report["quarantined"] >= 1
    assert "synthetic-hash" not in str(report)
    assert "hashed_password" not in first.person.to_dict()


def test_username_ownership_race_is_domain_conflict_with_usable_transaction(
    db_session, monkeypatch
):
    first = _member(db_session)
    second = _member(db_session)
    claimed = "canonical-" + uuid.uuid4().hex
    second.person.username = claimed
    db_session.flush()
    original = first.person.username
    scalar = db_session.scalar

    def concurrent_availability(statement, *args, **kwargs):
        # Emulate an ownership claim committing between the availability check
        # and our UPDATE. The actual unique index must still decide the race.
        sql = str(statement)
        if "person.username =" in sql and "person.id !=" in sql:
            return None
        return scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", concurrent_availability)
    with pytest.raises(IdentityConflictError, match="already owned"):
        crud_person.update_profile(
            db_session,
            membership_id=first.id,
            changes={"username": claimed},
            commit=False,
        )
    db_session.refresh(first.person)
    assert first.person.username == original
    assert crud_person.get(db_session, id=second.person_id).username == claimed


def test_revoke_identity_retires_opaque_tokens_codes_and_cli_sessions(db_session):
    first = _member(db_session)
    second = _member(db_session, person=first.person)
    access = models.OAuthMCPAccessToken(
        user_id=second.id,
        account_id=second.account_id,
        token_hash=uuid.uuid4().hex,
        client_id="example-client",
        scopes=[],
    )
    refresh = models.OAuthMCPRefreshToken(
        user_id=first.id,
        account_id=first.account_id,
        token_hash=uuid.uuid4().hex,
        client_id="example-client",
        scopes=[],
    )
    code = models.OAuthMCPAuthorizationCode(
        user_id=second.id,
        account_id=second.account_id,
        code_hash=uuid.uuid4().hex,
        client_id="example-client",
        redirect_uri="https://example.com/callback",
        code_challenge="synthetic-challenge",
        scopes=[],
        expires_at=9999999999,
    )
    cli = models.CliSession(user_id=second.id, refresh_jti=uuid.uuid4().hex)
    db_session.add_all([access, refresh, code, cli])
    db_session.flush()
    code_id = code.id
    crud_person.revoke_sessions(db_session, person_id=first.person_id, commit=False)
    db_session.refresh(access)
    db_session.refresh(refresh)
    db_session.refresh(cli)
    assert access.is_revoked and refresh.is_revoked
    assert cli.revoked_at is not None
    assert db_session.get(models.OAuthMCPAuthorizationCode, code_id) is None


def test_legacy_crud_update_writes_canonical_identity(db_session):
    from preloop.models.crud import crud_user

    first = _member(db_session)
    second = _member(db_session, person=first.person)
    crud_user.update(
        db_session,
        db_obj=second,
        obj_in={"full_name": "Canonical Name", "hashed_password": "reset-hash"},
    )
    assert (
        crud_person.profile_for_membership(db_session, membership_id=first.id).full_name
        == "Canonical Name"
    )
    assert (
        crud_person.credential_owner(db_session, membership_id=first.id).hashed_password
        == "reset-hash"
    )
    db_session.refresh(first)
    assert first.hashed_password is None


def test_new_linked_membership_cannot_introduce_credentials(db_session):
    first = _member(db_session)
    account = models.Account(organization_name="Example secondary")
    db_session.add(account)
    db_session.flush()
    second = models.User(
        account_id=account.id,
        person_id=first.person_id,
        username=uuid.uuid4().hex,
        email=first.email,
        hashed_password="unproven-admin-password",
    )
    with db_session.begin_nested():
        db_session.add(second)
        with pytest.raises(ValueError, match="cannot introduce"):
            db_session.flush()
        db_session.expunge(second)


def test_audit_transaction_enforces_read_only_database_access(db_engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session

    with Session(db_engine, autoflush=False) as db:
        report = crud_person.audit(db, read_only=True)
        assert {
            "memberships",
            "passkeys",
            "provider_links",
            "retained_without_memberships",
        } <= report.keys()
        with pytest.raises(DBAPIError, match="read-only"):
            db.execute(
                text(
                    "INSERT INTO person(id,email_normalized) VALUES (gen_random_uuid(),'readonly@example.com')"
                )
            )
        db.rollback()


def test_initial_oauth_identity_registers_provider_owner_and_provenance(db_session):
    account = models.Account(organization_name="Example provider account")
    db_session.add(account)
    db_session.flush()
    subject = "subject-" + uuid.uuid4().hex
    user = models.User(
        account_id=account.id,
        username=uuid.uuid4().hex,
        email="oauth@example.com",
        user_source="oauth_github",
        oauth_provider="github",
        external_id=subject,
    )
    db_session.add(user)
    db_session.flush()
    owner = crud_person.get_by_provider(
        db_session, source="oauth_github", provider="github", subject=subject
    )
    assert owner.id == user.person_id
    from sqlalchemy import select

    link = db_session.scalar(
        select(models.PersonAuthIdentity).where(
            models.PersonAuthIdentity.person_id == owner.id
        )
    )
    assert link.origin_user_id == user.id
    assert link.subject == subject
