"""Profile reads replace a rotted Google avatar with another linked photo."""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_person
from preloop.services.sso_avatar import refresh_anonymous_sso_avatar

_DEFAULT = "https://lh3.googleusercontent.com/a/default-user=s96-c"
_GITHUB = "https://avatars.githubusercontent.com/u/42?v=4"


def _user(db_session: Session, *, avatar_url: str | None, source: str | None):
    account = models.Account(organization_name="Example")
    db_session.add(account)
    db_session.flush()
    user = models.User(
        account_id=account.id,
        username=uuid4().hex,
        email=f"{uuid4().hex}@example.com",
        avatar_url=avatar_url,
        avatar_source=source,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _refresh(db_session: Session, user: models.User) -> bool:
    person = user.person
    return refresh_anonymous_sso_avatar(
        db_session,
        membership_id=user.id,
        person_id=person.id,
        avatar_url=person.avatar_url,
        avatar_source=person.avatar_source,
    )


class TestRefreshAnonymousSsoAvatar:
    def test_transient_lookup_does_not_clear_the_placeholder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "preloop.services.sso_avatar._replacement_avatar",
            lambda *args, **kwargs: (None, False),
        )

        def forbid_write(*args, **kwargs):
            raise AssertionError("placeholder was cleared during a failed lookup")

        monkeypatch.setattr(
            "preloop.services.sso_avatar.crud_person.update_profile", forbid_write
        )
        assert (
            refresh_anonymous_sso_avatar(
                None,  # type: ignore[arg-type]
                membership_id="membership",
                person_id="person",
                avatar_url=_DEFAULT,
                avatar_source="sso",
            )
            is False
        )

    def test_stores_the_linked_github_avatar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}
        monkeypatch.setattr(
            "preloop.services.sso_avatar._replacement_avatar",
            lambda *args, **kwargs: (_GITHUB, True),
        )

        def update(db, *, membership_id, changes, commit=True):
            seen["changes"] = changes

        monkeypatch.setattr(
            "preloop.services.sso_avatar.crud_person.update_profile", update
        )
        assert (
            refresh_anonymous_sso_avatar(
                None,  # type: ignore[arg-type]
                membership_id="membership",
                person_id="person",
                avatar_url=_DEFAULT,
                avatar_source="sso",
            )
            is True
        )
        assert seen["changes"] == {"avatar_url": _GITHUB, "avatar_source": "sso"}

    def test_replaces_google_placeholder_with_linked_github(
        self, db_session: Session
    ) -> None:
        user = _user(db_session, avatar_url=_DEFAULT, source="sso")
        crud_person.link_provider(
            db_session,
            person_id=user.person_id,
            source="oauth_github",
            provider="github",
            subject="42",
            origin_user_id=user.id,
            commit=False,
        )
        assert _refresh(db_session, user) is True
        db_session.refresh(user.person)
        assert user.person.avatar_url == _GITHUB
        assert user.person.avatar_source == "sso"

    def test_clears_placeholder_when_no_other_provider_is_linked(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "preloop.services.sso_avatar._fresh_google_picture",
            lambda db, mid: (None, True),
        )
        user = _user(db_session, avatar_url=_DEFAULT, source="sso")
        assert _refresh(db_session, user) is True
        db_session.refresh(user.person)
        assert user.person.avatar_url is None
        assert user.person.avatar_source is None

    def test_manual_upload_wins(self, db_session: Session) -> None:
        user = _user(db_session, avatar_url=_DEFAULT, source="manual")
        assert _refresh(db_session, user) is False
        assert user.person.avatar_url == _DEFAULT

    def test_fresh_google_picture_beats_the_placeholder(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = "https://avatars.githubusercontent.com/u/9?v=4"
        monkeypatch.setattr(
            "preloop.services.sso_avatar._fresh_google_picture",
            lambda db, mid: (fresh, True),
        )
        user = _user(db_session, avatar_url=_DEFAULT, source="sso")
        assert _refresh(db_session, user) is True
        db_session.refresh(user.person)
        assert user.person.avatar_url == fresh

    def test_real_google_photo_is_not_replaced(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "preloop.services.sso_avatar.is_anonymous_provider_avatar",
            lambda url: False,
        )
        kept = "https://lh3.googleusercontent.com/a-/real-photo=s96-c"
        user = _user(db_session, avatar_url=kept, source="sso")
        assert _refresh(db_session, user) is False
        assert user.person.avatar_url == kept
