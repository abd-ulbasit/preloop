"""Avatar image processing for user profile images.

Handles validation, EXIF stripping, re-encoding, and resizing of uploaded
profile images. Produces a bounded square PNG stored as a base64 data URI.
"""

import base64
import hashlib
import io
import logging
import time
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Constraints
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB raw upload limit
MAX_DIMENSION = 256  # Output image bound (px)
# Upper bound on the decoded pixel count. A 5 MB upload can hold a highly
# compressed image that expands to hundreds of MB of RGBA buffer, so the pixel
# count is bounded from the header before anything is decoded.
MAX_PIXELS = 4096 * 4096
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

_TOO_MANY_PIXELS = f"Image dimensions too large. Maximum: {MAX_PIXELS} total pixels"

# SSO provider claim keys
SSO_AVATAR_CLAIMS = {
    "google": "picture",
    "github": "avatar_url",
    "gitlab": "avatar_url",
}


class AvatarValidationError(Exception):
    """Raised when an uploaded avatar fails validation."""


def validate_content_type(content_type: str) -> None:
    """Raise if the content type is not an accepted image type."""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AvatarValidationError(
            f"Unsupported image type: {content_type}. "
            f"Accepted: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )


def validate_size(data: bytes) -> None:
    """Raise if the raw upload exceeds the size limit."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarValidationError(
            f"Image too large ({len(data)} bytes). Maximum: {MAX_UPLOAD_BYTES} bytes"
        )


def process_avatar(data: bytes, content_type: str) -> str:
    """Validate, strip EXIF, resize to a bounded square, and return a data URI.

    Args:
        data: Raw image bytes.
        content_type: MIME type of the upload.

    Returns:
        A ``data:image/png;base64,...`` URI string.

    Raises:
        AvatarValidationError: On invalid input.
    """
    validate_content_type(content_type)
    validate_size(data)

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        raise AvatarValidationError(
            "Image processing unavailable (Pillow not installed)"
        )

    # Pillow only *warns* at its default MAX_IMAGE_PIXELS and raises at twice
    # that, so lower the limit as defence in depth and reject explicitly below.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    try:
        img = Image.open(io.BytesIO(data))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AvatarValidationError(_TOO_MANY_PIXELS)
    except Exception:
        raise AvatarValidationError("Unable to decode image")

    # Image.open only parses the header, so the declared size is known here
    # without having decoded any pixel data yet. Reject oversized images before
    # convert() allocates a full RGBA buffer.
    width, height = img.size
    if width * height > MAX_PIXELS:
        raise AvatarValidationError(_TOO_MANY_PIXELS)

    # Strip EXIF and other metadata by re-drawing onto a fresh canvas.
    try:
        img = img.convert("RGBA")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AvatarValidationError(_TOO_MANY_PIXELS)
    except Exception:
        raise AvatarValidationError("Unable to decode image")

    # Crop to center square then resize to bounded dimensions.
    width, height = img.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if side > MAX_DIMENSION:
        img = img.resize((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    # Re-encode as PNG (lossless, strips any original metadata).
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# SHA-256 of the PNGs Google serves for ``/a/default-user``. A rotted picture
# URL returns one of these files with HTTP 200. The digest depends on the
# requested size: ``=s96-c`` is the small asset, ``=s512-c`` and the unsized
# URL share the large one, and ``=s64-c`` / ``=s128-c`` are their own files.
GOOGLE_ANONYMOUS_AVATAR_SHA256 = (
    "653f73dde99fede7a5ebaed7a14dad72c299935d3b9a0bad7420769cc8186a32"
)
GOOGLE_ANONYMOUS_AVATAR_SHA256S = frozenset(
    {
        GOOGLE_ANONYMOUS_AVATAR_SHA256,
        "959856355107bbc8bd53b2979ddf281288188d0397e59bc044409cdac3380732",
        "adf8ef226f14c364f96769e83cd05622fd5a2ef1381d413e04b467c522fda378",
        "d88bafbb2e759709f9362826b410a5f9731294dc75cc102ff633c6d6e4445af5",
    }
)
_http_client: Any = None
_GOOGLE_AVATAR_HOSTS = {
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
}
_AVATAR_FETCH_BYTES = 1024 * 1024
_ANON_CACHE_REAL_SECONDS = 3600.0
_ANON_CACHE_MISS_SECONDS = 60.0
_anon_cache: dict[str, tuple[float, bool]] = {}


def is_google_avatar_host(url: str) -> bool:
    """Return whether ``url`` points at Google's profile-photo CDN."""
    host = urlparse(url).hostname
    return host in _GOOGLE_AVATAR_HOSTS


def image_media_type(data: bytes) -> Optional[str]:
    """Return a processable image type from magic bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _default_user_path(url: str) -> bool:
    path = urlparse(url).path
    return "/a/default-user" in path


def _cached_anonymous(url: str) -> Optional[bool]:
    cached = _anon_cache.get(url)
    if cached is None:
        return None
    expires_at, anonymous = cached
    if time.monotonic() >= expires_at:
        _anon_cache.pop(url, None)
        return None
    return anonymous


def _remember_anonymous(url: str, anonymous: bool, *, ttl: float) -> None:
    if len(_anon_cache) > 256:
        _anon_cache.clear()
    _anon_cache[url] = (time.monotonic() + ttl, anonymous)


def avatar_http_client() -> Any:
    """Return one shared client for avatar downloads and provider picture lookups.

    Opening a new client per request on the profile path spends a connection
    setup on every console load. ``httpx.Client`` is safe to share across threads.
    """
    global _http_client
    if _http_client is None:
        import httpx

        _http_client = httpx.Client(timeout=2.0, follow_redirects=True)
    return _http_client


def fetch_google_avatar(url: str) -> Optional[tuple[bytes, str]]:
    """Download an https avatar from Google's photo CDN.

    Args:
        url: Candidate avatar URL.

    Returns:
        Image bytes and the final URL after redirects, or None when the host
        is not allowed or the fetch fails. Redirects that leave the CDN are
        discarded.
    """
    if not is_google_avatar_host(url):
        return None
    try:
        response = avatar_http_client().get(url)
    except Exception:
        logger.debug("Avatar fetch failed", exc_info=True)
        return None
    final_url = str(response.url)
    if response.status_code != 200 or not is_google_avatar_host(final_url):
        return None
    body = response.content
    if not body or len(body) > _AVATAR_FETCH_BYTES:
        return None
    return body, final_url


def fetch_avatar_bytes(url: str) -> Optional[bytes]:
    """Download an https avatar from Google's photo CDN.

    Args:
        url: Candidate avatar URL.

    Returns:
        Image bytes, or None when the host is not allowed or the fetch fails.
    """
    fetched = fetch_google_avatar(url)
    if fetched is None:
        return None
    return fetched[0]


def is_anonymous_provider_avatar(url: str) -> bool:
    """Return whether ``url`` is Google's anonymous person glyph.

    The literal ``default-user`` path is anonymous without a download. Any
    other Google photo URL is anonymous when the redirect lands on that path
    or the bytes match a known default-user size. A failed download is not
    treated as anonymous, so a CDN blip cannot wipe a real photo. Confirmed
    real photos are remembered for an hour so the console profile read does
    not download them on every page.

    Args:
        url: Avatar URL stored for the person or offered by a provider.

    Returns:
        True when the URL is Google's placeholder silhouette.
    """
    if not is_google_avatar_host(url):
        return False
    if _default_user_path(url):
        return True
    cached = _cached_anonymous(url)
    if cached is not None:
        return cached
    fetched = fetch_google_avatar(url)
    if fetched is None:
        _remember_anonymous(url, False, ttl=_ANON_CACHE_MISS_SECONDS)
        return False
    body, final_url = fetched
    anonymous = _default_user_path(final_url) or (
        hashlib.sha256(body).hexdigest() in GOOGLE_ANONYMOUS_AVATAR_SHA256S
    )
    if not anonymous:
        _remember_anonymous(url, False, ttl=_ANON_CACHE_REAL_SECONDS)
    return anonymous


def extract_sso_avatar_url(provider: str, user_info: dict) -> Optional[str]:
    """Extract the avatar URL from an SSO provider's user-info payload.

    Args:
        provider: OAuth provider name (google, github, gitlab).
        user_info: The decoded user-info/ID-token claims dict.

    Returns:
        The avatar URL string, or None if not present. Only ``https://`` URLs
        are accepted: the console renders this URL directly, so a plaintext
        ``http://`` avatar would be MITM-able. All three supported providers
        serve avatars over https.
    """
    claim_key = SSO_AVATAR_CLAIMS.get(provider)
    if not claim_key:
        return None
    url = user_info.get(claim_key)
    if isinstance(url, str) and url.startswith("https://"):
        return url
    return None
