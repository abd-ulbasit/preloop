"""Repair profile photos that SSO stored as a provider hotlink.

Google picture URLs keep working as HTTP 200 after the photo itself is gone:
the CDN returns the anonymous silhouette. The console then shows that glyph
instead of a face. This module replaces that placeholder with a fresh Google
picture, or with the stable avatar of another linked provider.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from preloop.models.crud.person import IdentityProfileUpdate, crud_person
from preloop.services.avatar import (
    AvatarValidationError,
    fetch_avatar_bytes,
    image_media_type,
    is_anonymous_provider_avatar,
    is_google_avatar_host,
    process_avatar,
)

logger = logging.getLogger(__name__)

_GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def refresh_anonymous_sso_avatar(
    db: Session,
    *,
    membership_id: Any,
    person_id: Any,
    avatar_url: Optional[str],
    avatar_source: Optional[str],
) -> bool:
    """Replace a rotted Google avatar on the canonical person.

    Manual uploads and already-snapshotted data URIs are left alone. A Google
    URL that still returns a real photo is left alone. An anonymous Google
    image is replaced with a fresh provider picture or a linked GitHub or
    GitLab avatar, and cleared when none of those exist.

    Args:
        db: Database session.
        membership_id: Account membership whose profile is being read.
        person_id: Canonical person that owns the avatar.
        avatar_url: Avatar currently stored for that person.
        avatar_source: ``sso``, ``manual``, or None.

    Returns:
        True when the canonical avatar was written.
    """
    if avatar_source == "manual":
        return False
    url = avatar_url or ""
    if url.startswith("data:"):
        return False
    google = bool(url) and is_google_avatar_host(url)
    if url and not google:
        return False
    if not url and avatar_source != "sso":
        return False
    if google and not is_anonymous_provider_avatar(url):
        return False

    replacement, definitive = _replacement_avatar(
        db,
        membership_id=membership_id,
        person_id=person_id,
        skip_url=url,
    )
    if not definitive or replacement == url:
        return False
    stored = _durable_copy(replacement) if replacement else None
    changes: IdentityProfileUpdate = {
        "avatar_url": stored,
        "avatar_source": "sso" if stored else None,
    }
    crud_person.update_profile(db, membership_id=membership_id, changes=changes)
    logger.info("Replaced an anonymous SSO avatar")
    return True


def _replacement_avatar(
    db: Session,
    *,
    membership_id: Any,
    person_id: Any,
    skip_url: str,
) -> tuple[Optional[str], bool]:
    """Return a replacement URL and whether that answer is final.

    A provider lookup that fails open is not final: the anonymous URL stays
    so a later profile read can try again. No linked provider is final, and
    the caller clears the placeholder.
    """
    fresh, definitive = _fresh_google_picture(db, membership_id)
    if not definitive:
        return None, False
    if (
        fresh
        and fresh != skip_url
        and fresh.startswith("https://")
        and not is_anonymous_provider_avatar(fresh)
    ):
        return fresh, True
    github = _github_avatar(db, person_id)
    if github:
        return github, True
    gitlab = _gitlab_avatar(db, person_id)
    if gitlab:
        return gitlab, True
    return None, True


def _github_avatar(db: Session, person_id: Any) -> Optional[str]:
    subject = crud_person.provider_subject(db, person_id=person_id, provider="github")
    if subject and subject.isdigit():
        return f"https://avatars.githubusercontent.com/u/{subject}?v=4"
    return None


def _gitlab_avatar(db: Session, person_id: Any) -> Optional[str]:
    subject = crud_person.provider_subject(db, person_id=person_id, provider="gitlab")
    if not subject or not subject.isdigit():
        return None
    from preloop.config import settings

    base = settings.gitlab_oauth.base_url.rstrip("/")
    if not base.startswith("https://"):
        return None
    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{base}/api/v4/users/{subject}")
        if response.status_code != 200:
            return None
        avatar = response.json().get("avatar_url")
    except Exception:
        logger.debug("GitLab avatar lookup failed", exc_info=True)
        return None
    if isinstance(avatar, str) and _public_https_url(avatar):
        return avatar
    return None


def _fresh_google_picture(
    db: Session, membership_id: Any
) -> tuple[Optional[str], bool]:
    """Read the current Google picture with the stored sign-in token.

    Returns:
        The picture URL (or None) and whether the lookup finished. False
        means a token exists but Google could not be reached, so the caller
        should keep the current avatar and retry later.
    """
    try:
        from preloop.models.crud import crud_oauth_token
        from preloop.utils.encryption import decrypt_value

        row = crud_oauth_token.get_by_user_and_provider(
            db, provider="google", user_id=membership_id
        )
    except Exception:
        logger.debug("Google avatar token lookup failed", exc_info=True)
        return None, False
    if row is None:
        return None, True
    try:
        access = decrypt_value(row.access_token_encrypted)
        picture = _userinfo_picture(access) if access else None
        if picture is None and row.refresh_token_encrypted:
            refreshed = _refresh_google_access(
                decrypt_value(row.refresh_token_encrypted)
            )
            picture = _userinfo_picture(refreshed) if refreshed else None
        return picture, True
    except Exception:
        logger.debug("Google avatar refresh failed", exc_info=True)
        return None, False


def _userinfo_picture(access_token: str) -> Optional[str]:
    import httpx

    with httpx.Client(timeout=2.0) as client:
        response = client.get(
            _GOOGLE_USERINFO,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        return None
    picture = response.json().get("picture")
    if isinstance(picture, str) and picture.startswith("https://"):
        return picture
    return None


def _refresh_google_access(refresh_token: str) -> Optional[str]:
    from preloop.config import settings

    client_id = settings.google_oauth.client_id
    client_secret = settings.google_oauth.client_secret
    if not refresh_token or not client_id or not client_secret:
        return None
    import httpx

    with httpx.Client(timeout=2.0) as client:
        response = client.post(
            _GOOGLE_TOKEN,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code != 200:
        return None
    access = response.json().get("access_token")
    return access if isinstance(access, str) else None


def _durable_copy(url: str) -> str:
    """Store Google photos as image bytes so a later CDN expiry cannot blank them."""
    if not is_google_avatar_host(url):
        return url
    body = fetch_avatar_bytes(url)
    if not body:
        return url
    media_type = image_media_type(body)
    if media_type is None:
        return url
    try:
        return process_avatar(body, media_type)
    except AvatarValidationError:
        return url


def _public_https_url(url: str) -> bool:
    import ipaddress

    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme != "https" or not host or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    )
