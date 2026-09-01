from __future__ import annotations

from pathlib import Path

import httpx


async def test_plaintext_is_absent_from_handles_tokens_responses_and_telemetry(
    build_service,
    identity_headers,
) -> None:
    plaintext = "provider-key-SENTINEL-never-return"
    outbound_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outbound_keys.append(request.headers["x-api-key"])
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Isolated",
                        "url": "https://example.com/isolated",
                        "highlights": ["no credentials here"],
                    }
                ]
            },
        )

    container, client = build_service(handler)
    async with client:
        credential_response = await client.post(
            "/v1/credentials",
            headers=identity_headers,
            json={"provider": "exa", "api_key": plaintext, "name": "test key"},
        )
        assert credential_response.status_code == 201
        credential = credential_response.json()
        assert credential["id"].startswith("cred_exa_")
        assert plaintext not in credential_response.text

        listed = await client.get("/v1/credentials", headers=identity_headers)
        assert plaintext not in listed.text

        capability_response = await client.post(
            "/v1/capabilities",
            headers=identity_headers,
            json={
                "job_id": "job_isolated",
                "credentials": {"exa": credential["id"]},
                "expires_in_seconds": 300,
            },
        )
        capability = capability_response.json()
        assert plaintext not in capability_response.text

        result = await client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {capability['token']}"},
            json={"query": "credential isolation", "provider": "exa", "job_id": "job_isolated"},
        )
        assert result.status_code == 200
        assert plaintext not in result.text
        assert outbound_keys == [plaintext]

        telemetry = await client.get(
            f"/v1/requests/{result.json()['request_id']}",
            headers=identity_headers,
        )
        assert plaintext not in telemetry.text

    database_bytes = Path(container.settings.database_path).read_bytes()
    assert plaintext.encode() not in database_bytes


async def test_capability_is_job_provider_and_owner_scoped(
    build_service,
    identity_headers,
    gateway_headers,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _, client = build_service(handler)
    async with client:
        credential = (
            await client.post(
                "/v1/credentials",
                headers=identity_headers,
                json={"provider": "exa", "api_key": "secret"},
            )
        ).json()
        denied = await client.post(
            "/v1/capabilities",
            headers=gateway_headers("other", "org_test"),
            json={"job_id": "job_1", "credentials": {"exa": credential["id"]}},
        )
        assert denied.status_code == 403

        capability = (
            await client.post(
                "/v1/capabilities",
                headers=identity_headers,
                json={"job_id": "job_1", "credentials": {"exa": credential["id"]}},
            )
        ).json()["token"]
        wrong_job = await client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {capability}"},
            json={"query": "x", "provider": "exa", "job_id": "job_2"},
        )
        assert wrong_job.status_code == 401

        wrong_provider = await client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {capability}"},
            json={"query": "x", "provider": "brave", "job_id": "job_1"},
        )
        assert wrong_provider.status_code == 502
        assert wrong_provider.json()["error"]["code"] == "credential_unavailable"

        tampered = capability[:-1] + ("A" if capability[-1] != "A" else "B")
        tampered_response = await client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {tampered}"},
            json={"query": "x", "provider": "exa", "job_id": "job_1"},
        )
        assert tampered_response.status_code == 401


async def test_delete_credential_removes_it_from_listing(build_service, identity_headers) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _, client = build_service(handler)
    async with client:
        credential = (
            await client.post(
                "/v1/credentials",
                headers=identity_headers,
                json={"provider": "brave", "api_key": "secret"},
            )
        ).json()
        deleted = await client.delete(
            f"/v1/credentials/{credential['id']}",
            headers=identity_headers,
        )
        listed = await client.get("/v1/credentials", headers=identity_headers)
    assert deleted.status_code == 204
    assert listed.json() == []
