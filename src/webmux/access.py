from __future__ import annotations

from abc import ABC, abstractmethod

from webmux.capabilities import CapabilityClaims
from webmux.credentials import CredentialBinding, CredentialVault
from webmux.models import Principal


class CredentialAccess(ABC):
    principal: Principal
    job_id: str | None

    @abstractmethod
    def binding_for(self, provider: str) -> CredentialBinding | None: ...


class PrincipalCredentialAccess(CredentialAccess):
    def __init__(self, vault: CredentialVault, principal: Principal, job_id: str | None) -> None:
        self.vault = vault
        self.principal = principal
        self.job_id = job_id

    def binding_for(self, provider: str) -> CredentialBinding | None:
        return self.vault.latest_binding(self.principal, provider)


class CapabilityCredentialAccess(CredentialAccess):
    def __init__(self, claims: CapabilityClaims) -> None:
        self.claims = claims
        self.principal = claims.principal
        self.job_id = claims.job_id

    def binding_for(self, provider: str) -> CredentialBinding | None:
        return self.claims.binding_for(provider)
