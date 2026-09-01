from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from webmux.access import CapabilityCredentialAccess, PrincipalCredentialAccess
from webmux.capabilities import CapabilityError
from webmux.config import Settings
from webmux.container import Container
from webmux.credentials import CredentialOwnershipError
from webmux.gateway import (
    ORG_ID_HEADER,
    SIGNATURE_HEADER,
    USER_ID_HEADER,
    verify_identity,
)
from webmux.health import tenant_scope
from webmux.models import (
    CapabilityCreate,
    CapabilityView,
    CredentialCreate,
    CredentialView,
    ErrorDetail,
    Principal,
    ProviderHealthView,
    ProviderView,
    SearchRequest,
    SearchResponse,
)
from webmux.router import RoutingError


def verified_principal(
    settings: Settings,
    user_id: str | None,
    org_id: str | None,
    signature: str | None,
) -> Principal:
    """Accept an identity only when the gateway proved it set the headers itself.

    The identity headers alone are a vault-admin credential and authorize spending a
    tenant's provider keys, so an unsigned pair is worth exactly nothing here. With
    no secret configured WebMux refuses identity requests rather than serving open.
    """
    if not user_id or not org_id:
        raise HTTPException(status_code=401, detail="authenticated user and org are required")
    if settings.gateway_secret is None:
        raise HTTPException(
            status_code=503,
            detail="gateway identity verification is not configured",
        )
    if not signature or not verify_identity(settings.gateway_secret, user_id, org_id, signature):
        raise HTTPException(status_code=401, detail="invalid gateway identity signature")
    return Principal(user_id=user_id, org_id=org_id)


async def require_principal(
    request: Request,
    user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
    org_id: Annotated[str | None, Header(alias=ORG_ID_HEADER)] = None,
    signature: Annotated[str | None, Header(alias=SIGNATURE_HEADER)] = None,
) -> Principal:
    return verified_principal(
        request.app.state.container.settings,
        user_id,
        org_id,
        signature,
    )


def create_app(
    settings: Settings | None = None,
    *,
    container: Container | None = None,
) -> FastAPI:
    owned_container = container is None
    service = container or Container.build(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owned_container:
            await service.close()

    app = FastAPI(
        title="WebMux",
        version="0.1.0",
        description="Neutral BYOK routing across web-search providers",
        lifespan=lifespan,
    )
    app.state.container = service

    @app.exception_handler(RoutingError)
    async def routing_error_handler(_: Request, exc: RoutingError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": ErrorDetail(
                    code=exc.code,
                    message=exc.safe_message,
                    provider=exc.provider,
                ).model_dump(),
                "request_id": exc.request_id,
            },
        )

    @app.post("/v1/search", response_model=SearchResponse)
    async def search(payload: SearchRequest, request: Request) -> SearchResponse:
        authorization = request.headers.get("authorization")
        if authorization:
            scheme, separator, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not separator or not token:
                raise HTTPException(status_code=401, detail="invalid authorization header")
            try:
                claims = service.capabilities.verify(token, expected_job_id=payload.job_id)
            except CapabilityError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            access = CapabilityCredentialAccess(claims)
        else:
            access = PrincipalCredentialAccess(
                service.vault,
                principal_from_request(request),
                payload.job_id,
            )
        return await service.router.search(payload, access)

    @app.post(
        "/v1/credentials",
        response_model=CredentialView,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_credential(
        payload: CredentialCreate,
        owner: Annotated[Principal, Depends(require_principal)],
    ) -> CredentialView:
        return service.vault.create(owner, payload.provider, payload.api_key, payload.name)

    @app.get("/v1/credentials", response_model=list[CredentialView])
    async def list_credentials(
        owner: Annotated[Principal, Depends(require_principal)],
    ) -> list[CredentialView]:
        return service.vault.list(owner)

    @app.delete("/v1/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_credential(
        credential_id: str,
        owner: Annotated[Principal, Depends(require_principal)],
    ) -> Response:
        if not service.vault.delete(owner, credential_id):
            raise HTTPException(status_code=404, detail="credential not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/capabilities",
        response_model=CapabilityView,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_capability(
        payload: CapabilityCreate,
        owner: Annotated[Principal, Depends(require_principal)],
    ) -> CapabilityView:
        try:
            return service.capabilities.issue(owner, payload)
        except CredentialOwnershipError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except CapabilityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/providers", response_model=list[ProviderView])
    async def list_providers(
        _: Annotated[Principal, Depends(require_principal)],
    ) -> list[ProviderView]:
        return [
            ProviderView(
                name=name,
                enabled=service.settings.provider_configs[name].enabled,
                estimated_cost=service.settings.provider_configs[name].estimated_cost,
            )
            for name in service.providers
        ]

    @app.get("/v1/providers/health", response_model=list[ProviderHealthView])
    async def provider_health(
        owner: Annotated[Principal, Depends(require_principal)],
    ) -> list[ProviderHealthView]:
        # One tenant's own measurements, never a cross-tenant aggregate.
        return service.health.report(tenant_scope(owner))

    @app.get("/v1/requests/{request_id}")
    async def get_request(
        request_id: str,
        owner: Annotated[Principal, Depends(require_principal)],
    ) -> dict[str, Any]:
        result = service.telemetry.get_request(request_id, owner)
        if result is None:
            raise HTTPException(status_code=404, detail="request not found")
        return result

    def principal_from_request(request: Request) -> Principal:
        return verified_principal(
            service.settings,
            request.headers.get(USER_ID_HEADER),
            request.headers.get(ORG_ID_HEADER),
            request.headers.get(SIGNATURE_HEADER),
        )

    return app
