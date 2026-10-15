"""Tests for user avatar functionality.

Covers upload validation, precedence rules, and per-provider SSO claim mapping.
"""

import hashlib
import io
from pathlib import Path

import pytest
from uuid import uuid4

from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_user

from preloop.services.avatar import (
    GOOGLE_ANONYMOUS_AVATAR_SHA256,
    GOOGLE_ANONYMOUS_AVATAR_SHA256S,
    AvatarValidationError,
    extract_sso_avatar_url,
    fetch_avatar_bytes,
    is_anonymous_provider_avatar,
    process_avatar,
    validate_content_type,
    validate_size,
    MAX_PIXELS,
    MAX_UPLOAD_BYTES,
    SSO_AVATAR_CLAIMS,
)
from preloop.services import avatar as avatar_service


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


class TestValidateContentType:
    def test_accepts_png(self):
        validate_content_type("image/png")

    def test_accepts_jpeg(self):
        validate_content_type("image/jpeg")

    def test_accepts_webp(self):
        validate_content_type("image/webp")

    def test_accepts_gif(self):
        validate_content_type("image/gif")

    def test_rejects_svg(self):
        with pytest.raises(AvatarValidationError, match="Unsupported"):
            validate_content_type("image/svg+xml")

    def test_rejects_octet_stream(self):
        with pytest.raises(AvatarValidationError, match="Unsupported"):
            validate_content_type("application/octet-stream")


class TestValidateSize:
    def test_accepts_small_file(self):
        validate_size(b"x" * 1024)

    def test_rejects_oversized_file(self):
        with pytest.raises(AvatarValidationError, match="too large"):
            validate_size(b"x" * (MAX_UPLOAD_BYTES + 1))


class TestProcessAvatar:
    def _make_png(self, width: int = 100, height: int = 100) -> bytes:
        """Create a minimal test PNG image."""
        from PIL import Image

        img = Image.new("RGBA", (width, height), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_returns_data_uri(self):
        data = self._make_png()
        result = process_avatar(data, "image/png")
        assert result.startswith("data:image/png;base64,")

    def test_square_crop(self):
        """A non-square image is cropped to center square."""
        data = self._make_png(200, 100)
        result = process_avatar(data, "image/png")
        assert result.startswith("data:image/png;base64,")
        # Decode and check dimensions
        import base64
        from PIL import Image

        b64 = result.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert img.width == img.height

    def test_rejects_invalid_content_type(self):
        data = self._make_png()
        with pytest.raises(AvatarValidationError, match="Unsupported"):
            process_avatar(data, "text/plain")

    def test_rejects_oversized(self):
        with pytest.raises(AvatarValidationError, match="too large"):
            process_avatar(b"x" * (MAX_UPLOAD_BYTES + 1), "image/png")

    def test_rejects_corrupt_data(self):
        with pytest.raises(AvatarValidationError, match="Unable to decode"):
            process_avatar(b"not-an-image", "image/png")

    def test_strips_exif(self):
        """Processed images have no metadata besides essential PNG chunks."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), "blue")
        buf = io.BytesIO()
        from PIL.ExifTags import Base as ExifBase

        exif = img.getexif()
        exif[ExifBase.Software] = "TestSuite"
        img.save(buf, format="JPEG", exif=exif.tobytes())
        raw_jpeg = buf.getvalue()

        result = process_avatar(raw_jpeg, "image/jpeg")
        # Output is PNG, re-open and verify no EXIF
        import base64

        b64 = result.split(",", 1)[1]
        out = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert not out.getexif(), "EXIF data should be stripped"

    def test_large_image_resized(self):
        """Images larger than MAX_DIMENSION are resized down."""
        data = self._make_png(1024, 1024)
        result = process_avatar(data, "image/png")
        import base64
        from PIL import Image

        b64 = result.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert img.width <= 256
        assert img.height <= 256


class TestDecompressionBomb:
    """The decoded pixel count is bounded before any full decode happens."""

    def _png_header(self, width: int, height: int) -> bytes:
        """A tiny PNG whose header declares ``width`` x ``height``.

        ``Image.open`` only parses the header, so a payload of a few hundred
        bytes is enough to exercise the pixel-count guard. If the guard ever
        regresses to checking dimensions after ``convert()``, these tests would
        try to allocate the full buffer instead of raising.
        """
        import struct
        import zlib

        def chunk(kind: bytes, payload: bytes) -> bytes:
            crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", crc)
            )

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
            + chunk(b"IEND", b"")
        )

    def test_payload_stays_tiny(self):
        """Guard the premise: the bomb payload is orders of magnitude smaller."""
        bomb = self._png_header(5000, 5000)
        assert len(bomb) < 1024
        assert 5000 * 5000 > MAX_PIXELS

    def test_rejects_bomb_above_pixel_limit(self):
        """25M declared pixels is rejected by the explicit pre-decode check.

        This size sits above ``MAX_PIXELS`` but below the 2x threshold where
        Pillow raises on its own, so it covers our own guard specifically.
        """
        bomb = self._png_header(5000, 5000)
        with pytest.raises(AvatarValidationError, match="too large"):
            process_avatar(bomb, "image/png")

    def test_rejects_extreme_bomb(self):
        """Pillow's own DecompressionBombError surfaces as a validation error."""
        bomb = self._png_header(30000, 30000)
        with pytest.raises(AvatarValidationError, match="too large"):
            process_avatar(bomb, "image/png")

    def test_pillow_pixel_limit_is_lowered(self):
        """Pillow's global bomb threshold is tightened to our own bound."""
        from PIL import Image

        img = Image.new("RGBA", (32, 32), (0, 128, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        process_avatar(buf.getvalue(), "image/png")

        assert Image.MAX_IMAGE_PIXELS == MAX_PIXELS

    def test_image_under_limit_still_processes(self):
        """The guard does not reject ordinary avatars."""
        from PIL import Image

        img = Image.new("RGBA", (512, 512), (0, 0, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = process_avatar(buf.getvalue(), "image/png")
        assert result.startswith("data:image/png;base64,")


class TestAvatarUploadReadLimit:
    """The upload endpoint must not buffer an oversized body before rejecting."""

    def test_chunked_read_rejects_over_max(self):
        import asyncio

        from fastapi import HTTPException

        from preloop.api.endpoints.account import _read_upload_with_limit

        class FakeUpload:
            def __init__(self, total: int) -> None:
                self.sent = 0
                self.total = total

            async def read(self, n: int = -1) -> bytes:
                if self.sent >= self.total:
                    return b""
                take = min(n if n > 0 else self.total, self.total - self.sent)
                self.sent += take
                return b"x" * take

        fake = FakeUpload(MAX_UPLOAD_BYTES + 1)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(_read_upload_with_limit(fake, MAX_UPLOAD_BYTES))
        assert exc_info.value.status_code == 413
        assert fake.sent <= MAX_UPLOAD_BYTES + 1024 * 1024


class TestPillowDependencyDeclared:
    """Pillow is a declared runtime dependency, not a best-effort import."""

    def test_pillow_importable(self):
        from PIL import Image  # noqa: F401

    def test_pillow_declared_in_pyproject(self):
        """A clean ``pip install -e ".[dev]"`` must provide PIL."""
        import tomllib
        from pathlib import Path

        # backend/tests/test_avatar.py -> repo root
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        dependencies = data["project"]["dependencies"]

        assert any(dep.lower().startswith("pillow") for dep in dependencies), (
            "Pillow must be declared in [project].dependencies so the avatar "
            "service is not silently unavailable on a clean install."
        )

    @pytest.mark.parametrize(
        "lock",
        [
            "requirements/runtime.txt",
            ".github/requirements/app-dev.txt",
            ".github/requirements/runtime-plugin-test.txt",
        ],
    )
    def test_pillow_pinned_in_locks_compiled_from_pyproject(self, lock: str):
        """Declaring the dependency is not enough; the locks are what get installed.

        CI installs ``--require-hashes -r .github/requirements/app-dev.txt``
        and the image installs ``requirements/runtime.txt``, so a lock that was
        not recompiled after a pyproject change fails at import time with no
        hint about the cause.
        """
        from pathlib import Path

        # backend/tests/test_avatar.py -> repo root
        text = (Path(__file__).resolve().parents[2] / lock).read_text(encoding="utf-8")
        assert "\npillow==" in text, (
            f"{lock} is compiled from pyproject.toml but has no pillow pin. "
            "Recompile it with the uv command in its header comment."
        )


# ---------------------------------------------------------------------------
# SSO claim mapping
# ---------------------------------------------------------------------------


class TestExtractSsoAvatarUrl:
    def test_google_picture(self):
        url = extract_sso_avatar_url(
            "google", {"picture": "https://lh3.googleusercontent.com/photo"}
        )
        assert url == "https://lh3.googleusercontent.com/photo"

    def test_github_avatar_url(self):
        url = extract_sso_avatar_url(
            "github", {"avatar_url": "https://avatars.githubusercontent.com/u/123"}
        )
        assert url == "https://avatars.githubusercontent.com/u/123"

    def test_gitlab_avatar_url(self):
        url = extract_sso_avatar_url(
            "gitlab", {"avatar_url": "https://gitlab.com/uploads/-/avatar.png"}
        )
        assert url == "https://gitlab.com/uploads/-/avatar.png"

    def test_missing_claim_returns_none(self):
        url = extract_sso_avatar_url("google", {"email": "user@example.com"})
        assert url is None

    def test_unknown_provider_returns_none(self):
        url = extract_sso_avatar_url(
            "unknown", {"picture": "https://example.com/pic.jpg"}
        )
        assert url is None

    def test_non_http_value_returns_none(self):
        url = extract_sso_avatar_url("google", {"picture": "not-a-url"})
        assert url is None

    def test_plaintext_http_returns_none(self):
        """A plaintext avatar URL is MITM-able, so it is not accepted."""
        url = extract_sso_avatar_url("google", {"picture": "http://example.com/p.jpg"})
        assert url is None

    def test_scheme_lookalike_returns_none(self):
        """`startswith("http")` used to accept this; an explicit scheme does not."""
        for value in ("httpfoo", "httpfoo://example.com/p.jpg", "https:/example.com"):
            assert extract_sso_avatar_url("google", {"picture": value}) is None

    def test_non_string_claim_returns_none(self):
        url = extract_sso_avatar_url("google", {"picture": {"url": "https://x/y"}})
        assert url is None

    def test_claim_keys_documented(self):
        """All three providers have documented claim keys."""
        assert "google" in SSO_AVATAR_CLAIMS
        assert "github" in SSO_AVATAR_CLAIMS
        assert "gitlab" in SSO_AVATAR_CLAIMS


class TestAnonymousProviderAvatar:
    """Google's default silhouette is not a profile photo."""

    def setup_method(self) -> None:
        avatar_service._anon_cache.clear()

    def test_default_user_path_is_anonymous_without_a_download(self, monkeypatch):
        def fail(url: str) -> bytes:
            raise AssertionError(url)

        monkeypatch.setattr(avatar_service, "fetch_avatar_bytes", fail)
        url = "https://lh3.googleusercontent.com/a/default-user=s96-c"
        assert is_anonymous_provider_avatar(url) is True

    def test_matching_bytes_are_anonymous(self, monkeypatch):
        body = b"default-silhouette"
        digest = hashlib.sha256(body).hexdigest()
        monkeypatch.setattr(
            avatar_service,
            "fetch_google_avatar",
            lambda url: (body, url),
        )
        monkeypatch.setattr(
            avatar_service,
            "GOOGLE_ANONYMOUS_AVATAR_SHA256S",
            frozenset({digest}),
        )
        url = "https://lh3.googleusercontent.com/a-/rotten=s96-c"
        assert is_anonymous_provider_avatar(url) is True

    def test_s512_placeholder_bytes_are_anonymous_without_the_default_path(
        self, monkeypatch
    ):
        """A rotted ``a-/`` URL is anonymous when the body is the 512px glyph."""
        body = (
            Path(__file__).resolve().parent / "fixtures" / "google-anonymous-s512.png"
        ).read_bytes()
        assert hashlib.sha256(body).hexdigest() in GOOGLE_ANONYMOUS_AVATAR_SHA256S
        monkeypatch.setattr(
            avatar_service,
            "fetch_google_avatar",
            lambda url: (body, url),
        )
        url = "https://lh3.googleusercontent.com/a-/rotted-picture=s512-c"
        assert "/a/default-user" not in url
        assert is_anonymous_provider_avatar(url) is True

    def test_redirect_to_default_user_is_anonymous(self, monkeypatch):
        monkeypatch.setattr(
            avatar_service,
            "fetch_google_avatar",
            lambda url: (
                b"not-the-known-bytes",
                "https://lh3.googleusercontent.com/a/default-user=s96-c",
            ),
        )
        url = "https://lh3.googleusercontent.com/a-/rotted-picture"
        assert is_anonymous_provider_avatar(url) is True

    def test_other_image_bytes_are_kept(self, monkeypatch):
        monkeypatch.setattr(
            avatar_service, "fetch_google_avatar", lambda url: (b"face", url)
        )
        url = "https://lh3.googleusercontent.com/a-/real=s96-c"
        assert is_anonymous_provider_avatar(url) is False
        assert GOOGLE_ANONYMOUS_AVATAR_SHA256 in GOOGLE_ANONYMOUS_AVATAR_SHA256S

    def test_download_failure_is_not_anonymous(self, monkeypatch):
        monkeypatch.setattr(avatar_service, "fetch_google_avatar", lambda url: None)
        url = "https://lh4.googleusercontent.com/a-/maybe=s96-c"
        assert is_anonymous_provider_avatar(url) is False

    def test_non_google_host_is_not_anonymous(self, monkeypatch):
        def fail(url: str) -> bytes:
            raise AssertionError(url)

        monkeypatch.setattr(avatar_service, "fetch_avatar_bytes", fail)
        assert (
            is_anonymous_provider_avatar("https://avatars.githubusercontent.com/u/1")
            is False
        )

    def test_fetch_refuses_a_non_google_host(self):
        assert fetch_avatar_bytes("https://example.com/a.png") is None


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


class TestAvatarPrecedence:
    """Test that manual upload > SSO > default via the CRUD helper."""

    @pytest.mark.parametrize(
        ("source", "previous_url"),
        [
            (None, None),
            ("manual", "data:image/png;base64,AAAA"),
            ("sso", "https://old.example.com/pic.jpg"),
        ],
    )
    def test_sso_uses_canonical_precedence_across_memberships(
        self, db_session: Session, source: str | None, previous_url: str | None
    ) -> None:
        """A stale membership cache cannot override the identity's manual upload."""
        accounts = [models.Account(organization_name="Example") for _ in range(2)]
        db_session.add_all(accounts)
        db_session.flush()
        first = models.User(
            account_id=accounts[0].id,
            username=uuid4().hex,
            email="avatar@example.com",
            avatar_source=source,
            avatar_url=previous_url,
        )
        db_session.add(first)
        db_session.flush()
        second = models.User(
            account_id=accounts[1].id,
            person=first.person,
            username=uuid4().hex,
            email=first.email,
            avatar_source="sso",
            avatar_url="https://stale.example.com/pic.jpg",
        )
        db_session.add(second)
        db_session.flush()
        original_ids = (first.id, second.id)
        new_url = "https://new.example.com/pic.jpg"
        crud_user.set_avatar_from_sso(
            db_session, user=second, avatar_url=new_url, commit=False
        )
        assert first.person.avatar_url == (
            previous_url if source == "manual" else new_url
        )
        assert first.person.avatar_source == ("manual" if source == "manual" else "sso")
        if source != "manual":
            db_session.refresh(first)
            db_session.refresh(second)
            assert first.avatar_url == second.avatar_url == new_url
        assert (first.id, second.id) == original_ids

    def test_anonymous_google_avatar_does_not_replace_a_photo(
        self, db_session: Session
    ) -> None:
        account = models.Account(organization_name="Example")
        db_session.add(account)
        db_session.flush()
        kept = "https://avatars.githubusercontent.com/u/123?v=4"
        user = models.User(
            account_id=account.id,
            username=uuid4().hex,
            email="avatar-anon@example.com",
            avatar_source="sso",
            avatar_url=kept,
        )
        db_session.add(user)
        db_session.flush()
        crud_user.set_avatar_from_sso(
            db_session,
            user=user,
            avatar_url="https://lh3.googleusercontent.com/a/default-user=s96-c",
            commit=False,
        )
        assert user.person.avatar_url == kept
        assert user.person.avatar_source == "sso"
