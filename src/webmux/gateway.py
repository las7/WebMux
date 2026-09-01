from __future__ import annotations

import base64
import hashlib
import hmac

USER_ID_HEADER = "X-WebMux-User-Id"
ORG_ID_HEADER = "X-WebMux-Org-Id"
SIGNATURE_HEADER = "X-WebMux-Gateway-Signature"

MIN_SECRET_LENGTH = 32


def coerce_secret(secret: str | bytes) -> bytes:
    """Normalize a configured gateway secret and reject weak values."""
    value = secret.encode("utf-8") if isinstance(secret, str) else secret
    if len(value) < MIN_SECRET_LENGTH:
        raise ValueError(f"gateway secret must be at least {MIN_SECRET_LENGTH} bytes")
    return value


def sign_identity(secret: bytes, user_id: str, org_id: str) -> str:
    """Signature the authenticating gateway attaches to the identity headers.

    The identity fields are length-prefixed so no pair of principals can produce the
    same signed message.
    """
    message = (
        f"webmux:v1:gateway-identity:{len(org_id)}:{org_id}:{len(user_id)}:{user_id}"
    ).encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_identity(secret: bytes, user_id: str, org_id: str, signature: str) -> bool:
    return hmac.compare_digest(sign_identity(secret, user_id, org_id), signature)
