from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.testclient import TestClient

from tools.family_auth import FamilyKeyRegistry
from tools.remote_gateway import create_app


class FakeSearchService:
    async def research_round(self, **kwargs):
        return {
            "query": kwargs["query"],
            "claims": [],
            "sources": [],
            "conversation_url": "https://private.invalid/chat/1",
            "provider_status": {"qianwen": "complete"},
        }

    def provider_status(self):
        return {
            "status": "ready",
            "providers": ["doubao", "qianwen"],
            "cdp_url": "http://127.0.0.1:9557",
        }


def _client(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    raw_key, _principal = registry.create("test-family")
    return TestClient(create_app(registry=registry, service=FakeSearchService())), raw_key


def test_health_is_public_and_auth_is_required(tmp_path):
    client, _key = _client(tmp_path)
    with client:
        assert client.get("/healthz").json() == {"status": "ok"}
        response = client.get("/v1/providers/status")
        assert response.status_code == 401
        assert "authorization" not in response.text.lower()


def test_rest_search_is_authenticated_and_redacted(tmp_path):
    client, key = _client(tmp_path)
    with client:
        response = client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": "1970文创园", "timeout": 10},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "1970文创园"
    assert "conversation_url" not in body
    assert "9557" not in response.text


def test_job_cannot_be_read_by_another_key(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    first, _ = registry.create("first")
    second, _ = registry.create("second")
    client = TestClient(create_app(registry=registry, service=FakeSearchService()))
    with client:
        created = client.post(
            "/v1/research",
            headers={"Authorization": f"Bearer {first}"},
            json={"query": "example", "wait_seconds": 0, "timeout": 10},
        )
        request_id = created.json()["request_id"]
        denied = client.get(
            f"/v1/research/{request_id}",
            headers={"Authorization": f"Bearer {second}"},
        )

    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_twenty_authenticated_research_clients_respect_global_limit(tmp_path):
    class TrackingService(FakeSearchService):
        def __init__(self):
            self.active = 0
            self.maximum = 0
            self.lock = asyncio.Lock()

        async def research_round(self, **kwargs):
            async with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.02)
            async with self.lock:
                self.active -= 1
            return await super().research_round(**kwargs)

    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    keys = [registry.create(f"client-{index}")[0] for index in range(20)]
    service = TrackingService()
    app = create_app(registry=registry, service=service)
    transport = httpx.ASGITransport(app=app)
    async with app.inner_app.router.lifespan_context(app.inner_app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/v1/search",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"query": f"query-{index}", "timeout": 10},
                    )
                    for index, key in enumerate(keys)
                )
            )

    assert all(response.status_code == 200 for response in responses)
    assert service.maximum == 2
    assert service.active == 0
