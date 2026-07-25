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
    return TestClient(
        create_app(registry=registry, service=FakeSearchService()),
        client=("127.0.0.1", 50000),
    ), raw_key


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
    client = TestClient(
        create_app(registry=registry, service=FakeSearchService()),
        client=("127.0.0.1", 50000),
    )
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


def test_one_key_accepts_ten_cloudflare_addresses_and_rejects_eleventh(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    key, _ = registry.create("shared", max_bound_addresses=10)
    client = TestClient(
        create_app(registry=registry, service=FakeSearchService()),
        client=("127.0.0.1", 50000),
    )
    with client:
        responses = [
            client.get(
                "/v1/providers/status",
                headers={
                    "Authorization": f"Bearer {key}",
                    "CF-Connecting-IP": f"203.0.113.{index}",
                },
            )
            for index in range(1, 11)
        ]
        repeated = client.get(
            "/v1/providers/status",
            headers={
                "Authorization": f"Bearer {key}",
                "CF-Connecting-IP": "203.0.113.1",
            },
        )
        rejected = client.get(
            "/v1/providers/status",
            headers={
                "Authorization": f"Bearer {key}",
                "CF-Connecting-IP": "203.0.113.11",
            },
        )

    assert all(response.status_code == 200 for response in responses)
    assert repeated.status_code == 200
    assert rejected.status_code == 403
    assert rejected.json()["error"] == "address_limit_exceeded"
    assert registry.list_records()[0]["bound_address_count"] == 10


@pytest.mark.asyncio
async def test_untrusted_peer_cannot_spoof_cloudflare_address(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    key, _ = registry.create("shared")
    app = create_app(registry=registry, service=FakeSearchService())
    transport = httpx.ASGITransport(app=app, client=("198.51.100.20", 123))
    async with app.inner_app.router.lifespan_context(app.inner_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://service") as client:
            for claimed in ("203.0.113.1", "203.0.113.2"):
                response = await client.get(
                    "/v1/providers/status",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "CF-Connecting-IP": claimed,
                    },
                )
                assert response.status_code == 200

    assert registry.list_records()[0]["bound_address_count"] == 1


@pytest.mark.asyncio
async def test_shared_key_jobs_are_isolated_and_limited_by_address(tmp_path):
    class BlockingService(FakeSearchService):
        def __init__(self):
            self.release = asyncio.Event()

        async def research_round(self, **kwargs):
            await self.release.wait()
            return await super().research_round(**kwargs)

    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    key, _ = registry.create("shared")
    service = BlockingService()
    app = create_app(registry=registry, service=service)
    transport = httpx.ASGITransport(app=app)
    first_headers = {
        "Authorization": f"Bearer {key}",
        "CF-Connecting-IP": "203.0.113.1",
    }
    second_headers = {
        "Authorization": f"Bearer {key}",
        "CF-Connecting-IP": "203.0.113.2",
    }
    async with app.inner_app.router.lifespan_context(app.inner_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://service") as client:
            first = await client.post(
                "/v1/research",
                headers=first_headers,
                json={"query": "first", "wait_seconds": 0, "timeout": 10},
            )
            second = await client.post(
                "/v1/research",
                headers=second_headers,
                json={"query": "second", "wait_seconds": 0, "timeout": 10},
            )
            duplicate = await client.post(
                "/v1/research",
                headers=first_headers,
                json={"query": "duplicate", "wait_seconds": 0, "timeout": 10},
            )
            cross_address = await client.get(
                f"/v1/research/{first.json()['request_id']}",
                headers=second_headers,
            )
            service.release.set()

    assert first.status_code == 202
    assert second.status_code == 202
    assert duplicate.status_code == 429
    assert cross_address.status_code == 404


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


@pytest.mark.asyncio
async def test_five_background_research_requests_queue_and_all_complete(tmp_path):
    class QueuedService(FakeSearchService):
        def __init__(self):
            self.active = 0
            self.maximum = 0
            self.release = asyncio.Event()
            self.lock = asyncio.Lock()

        async def research_round(self, **kwargs):
            async with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            await self.release.wait()
            async with self.lock:
                self.active -= 1
            return await super().research_round(**kwargs)

    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    keys = [registry.create(f"queued-{index}")[0] for index in range(5)]
    service = QueuedService()
    app = create_app(registry=registry, service=service)
    transport = httpx.ASGITransport(app=app)
    async with app.inner_app.router.lifespan_context(app.inner_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            created = await asyncio.gather(
                *(
                    client.post(
                        "/v1/research",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"query": f"query-{index}", "wait_seconds": 0, "timeout": 10},
                    )
                    for index, key in enumerate(keys)
                )
            )
            assert all(response.status_code == 202 for response in created)
            request_ids = [response.json()["request_id"] for response in created]
            for _attempt in range(100):
                if service.active == 2:
                    break
                await asyncio.sleep(0.01)
            states = await asyncio.gather(
                *(
                    client.get(
                        f"/v1/research/{request_id}",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    for request_id, key in zip(request_ids, keys)
                )
            )
            bodies = [response.json() for response in states]
            statuses = [body["status"] for body in bodies]
            assert statuses.count("running") == 2
            assert statuses.count("queued") == 3
            assert sorted(
                body["queue_position"] for body in bodies if body["status"] == "queued"
            ) == [1, 2, 3]
            service.release.set()
            for _attempt in range(200):
                finished = await asyncio.gather(
                    *(
                        client.get(
                            f"/v1/research/{request_id}",
                            headers={"Authorization": f"Bearer {key}"},
                        )
                        for request_id, key in zip(request_ids, keys)
                    )
                )
                if all(response.json()["status"] == "complete" for response in finished):
                    break
                await asyncio.sleep(0.01)

    assert service.maximum == 2
    assert all(response.json()["status"] == "complete" for response in finished)
