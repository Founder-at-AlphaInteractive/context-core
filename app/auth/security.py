"""Device authentication.

Design:
- A device is enrolled once via /auth/device/register using a shared
  server-side registration secret.
- The server issues an opaque random token (NOT a JWT). Only the SHA-256
  hash of the token is stored in the DB. This makes revocation trivial:
  set devices.revoked = true and the token is dead.
- Clients send the raw token as `Authorization: Bearer <token>`.
- The server hashes the presented token and looks up a non-revoked device.
"""

import hashlib
import hmac
import secrets

from app.config import settings


def generate_device_token() -> str:
    """Generate a high-entropy opaque device token (url-safe)."""
    return secrets.token_urlsafe(48)


def hash_device_token(raw_token: str) -> str:
    """Return a deterministic SHA-256 hex digest of the raw token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def verify_device_registration_secret(provided: str) -> bool:
    """Constant-time comparison against the configured registration secret."""
    expected = settings.device_registration_secret
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
