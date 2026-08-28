from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from webmux.models import CredentialView, Principal, utc_now
from webmux.providers.base import ProviderAuthenticationError
from webmux.storage import Store


class CredentialNotFoundError(Exception):
    pass


class CredentialOwnershipError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    handle: str
    provider: str


class CredentialVault:
    """Encrypts provider keys at rest and exposes only opaque handles externally."""

    def __init__(self, store: Store, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be exactly 32 bytes")
        self.store = store
        self._cipher = AESGCM(master_key)

    @staticmethod
    def _aad(handle: str, principal: Principal, provider: str) -> bytes:
        return f"webmux:v1:{handle}:{principal.org_id}:{principal.user_id}:{provider}".encode()

    def create(
        self,
        principal: Principal,
        provider: str,
        api_key: str,
        name: str | None = None,
    ) -> CredentialView:
        handle = f"cred_{provider}_{secrets.token_urlsafe(18)}"
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            api_key.encode("utf-8"),
            self._aad(handle, principal, provider),
        )
        created_at = utc_now()
        self.store.execute(
            """
            INSERT INTO credentials
                (id, user_id, org_id, provider, name, ciphertext, nonce, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handle,
                principal.user_id,
                principal.org_id,
                provider,
                name,
                ciphertext,
                nonce,
                created_at.isoformat(),
            ),
        )
        return CredentialView(
            id=handle,
            provider=provider,
            name=name,
            created_at=created_at,
        )

    def list(self, principal: Principal) -> list[CredentialView]:
        rows = self.store.fetchall(
            """
            SELECT id, provider, name, created_at, last_used_at
            FROM credentials
            WHERE user_id = ? AND org_id = ? AND deleted_at IS NULL
            ORDER BY created_at DESC
            """,
            (principal.user_id, principal.org_id),
        )
        return [
            CredentialView(
                id=row["id"],
                provider=row["provider"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
                last_used_at=(
                    datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None
                ),
            )
            for row in rows
        ]

    def delete(self, principal: Principal, handle: str) -> bool:
        cursor = self.store.execute(
            """
            UPDATE credentials SET deleted_at = ?
            WHERE id = ? AND user_id = ? AND org_id = ? AND deleted_at IS NULL
            """,
            (utc_now().isoformat(), handle, principal.user_id, principal.org_id),
        )
        return cursor.rowcount == 1

    def assert_owned(self, principal: Principal, handle: str, provider: str) -> None:
        row = self.store.fetchone(
            """
            SELECT provider FROM credentials
            WHERE id = ? AND user_id = ? AND org_id = ? AND deleted_at IS NULL
            """,
            (handle, principal.user_id, principal.org_id),
        )
        if row is None:
            raise CredentialOwnershipError("credential does not belong to this principal")
        if row["provider"] != provider:
            raise CredentialOwnershipError("credential provider does not match capability scope")

    def latest_binding(self, principal: Principal, provider: str) -> CredentialBinding | None:
        row = self.store.fetchone(
            """
            SELECT id FROM credentials
            WHERE user_id = ? AND org_id = ? AND provider = ? AND deleted_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (principal.user_id, principal.org_id, provider),
        )
        if row is None:
            return None
        return CredentialBinding(handle=row["id"], provider=provider)

    def resolve_for_provider(self, credential_handle: str, provider: str) -> str:
        """Trusted-provider boundary: never call this from an agent-facing serializer."""
        row = self.store.fetchone(
            """
            SELECT user_id, org_id, provider, ciphertext, nonce
            FROM credentials WHERE id = ? AND deleted_at IS NULL
            """,
            (credential_handle,),
        )
        if row is None or row["provider"] != provider:
            raise ProviderAuthenticationError(provider, "credential handle is unavailable")
        principal = Principal(user_id=row["user_id"], org_id=row["org_id"])
        try:
            plaintext = self._cipher.decrypt(
                row["nonce"],
                row["ciphertext"],
                self._aad(credential_handle, principal, provider),
            )
        except Exception as exc:
            raise ProviderAuthenticationError(
                provider,
                "credential could not be decrypted",
            ) from exc
        self.store.execute(
            "UPDATE credentials SET last_used_at = ? WHERE id = ?",
            (utc_now().isoformat(), credential_handle),
        )
        return plaintext.decode("utf-8")
