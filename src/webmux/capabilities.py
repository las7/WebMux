from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from webmux.credentials import CredentialBinding, CredentialVault
from webmux.models import CapabilityCreate, CapabilityView, Principal


class CapabilityError(Exception):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


@dataclass(frozen=True, slots=True)
class CapabilityClaims:
    principal: Principal
    job_id: str
    expires_at: datetime
    credentials: dict[str, str]
    operations: frozenset[str]
    token_id: str

    def binding_for(self, provider: str) -> CredentialBinding | None:
        operation = f"search.{provider}"
        handle = self.credentials.get(provider)
        if operation not in self.operations or not handle:
            return None
        return CredentialBinding(handle=handle, provider=provider)


class CapabilityBroker:
    """Issues short-lived, job-scoped bearer capabilities for secretless workloads."""

    def __init__(
        self,
        vault: CredentialVault,
        signing_key: bytes,
        *,
        max_ttl_seconds: int = 3_600,
    ) -> None:
        self.vault = vault
        self._signing_key = signing_key
        self.max_ttl_seconds = max_ttl_seconds

    def issue(self, principal: Principal, request: CapabilityCreate) -> CapabilityView:
        if request.expires_in_seconds > self.max_ttl_seconds:
            raise CapabilityError("capability TTL exceeds server maximum")
        for provider, handle in request.credentials.items():
            self.vault.assert_owned(principal, handle, provider)

        now = int(time.time())
        expires = now + request.expires_in_seconds
        operations = sorted(f"search.{provider}" for provider in request.credentials)
        payload = {
            "v": 1,
            "iss": "webmux",
            "sub": principal.user_id,
            "org": principal.org_id,
            "job": request.job_id,
            "iat": now,
            "exp": expires,
            "jti": f"cap_{secrets.token_urlsafe(12)}",
            "ops": operations,
            "creds": request.credentials,
        }
        header_segment = _b64encode(b'{"alg":"HS256","typ":"WMXCAP"}')
        payload_segment = _b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = _b64encode(hmac.new(self._signing_key, signing_input, hashlib.sha256).digest())
        return CapabilityView(
            token=f"{header_segment}.{payload_segment}.{signature}",
            job_id=request.job_id,
            expires_at=datetime.fromtimestamp(expires, UTC),
            allowed_operations=operations,
        )

    def verify(self, token: str, *, expected_job_id: str | None = None) -> CapabilityClaims:
        try:
            header_segment, payload_segment, signature_segment = token.split(".")
            header = json.loads(_b64decode(header_segment))
            payload: dict[str, Any] = json.loads(_b64decode(payload_segment))
            signature = _b64decode(signature_segment)
        except Exception as exc:
            raise CapabilityError("invalid capability token") from exc
        if header != {"alg": "HS256", "typ": "WMXCAP"}:
            raise CapabilityError("invalid capability header")
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        expected = hmac.new(self._signing_key, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise CapabilityError("invalid capability signature")

        required = {"v", "iss", "sub", "org", "job", "iat", "exp", "jti", "ops", "creds"}
        if set(payload) != required or payload["v"] != 1 or payload["iss"] != "webmux":
            raise CapabilityError("invalid capability claims")
        if not isinstance(payload["exp"], int) or payload["exp"] <= int(time.time()):
            raise CapabilityError("capability has expired")
        if payload["exp"] - payload["iat"] > self.max_ttl_seconds:
            raise CapabilityError("capability TTL is invalid")
        if expected_job_id is not None and payload["job"] != expected_job_id:
            raise CapabilityError("capability is not valid for this job")
        if not isinstance(payload["ops"], list) or not isinstance(payload["creds"], dict):
            raise CapabilityError("invalid capability scopes")

        return CapabilityClaims(
            principal=Principal(user_id=payload["sub"], org_id=payload["org"]),
            job_id=payload["job"],
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
            credentials=dict(payload["creds"]),
            operations=frozenset(payload["ops"]),
            token_id=payload["jti"],
        )
