"""Auth, health, CORS, problem+json, configs."""

from __future__ import annotations

import httpx
import pytest
from gwutil import INLINE_CONFIG, KEY

API = "/api/v1"


async def test_healthz_is_open_and_checks_the_database(gw) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw.app), base_url="http://test"
    ) as anon:
        r = await anon.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "UP"}


async def test_every_api_route_requires_a_valid_key(gw) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw.app), base_url="http://test"
    ) as anon:
        for headers in ({}, {"X-API-Key": "wrong-key-wrong-key-wrong"}):
            r = await anon.get(f"{API}/runs", headers=headers)
            assert r.status_code == 401
            assert r.headers["content-type"].startswith("application/problem+json")
            assert r.json()["status"] == 401 and "key" in r.json()["detail"].lower()
            assert r.headers["www-authenticate"] == "ApiKey"
        for method, path in [("GET", "/runs"), ("POST", "/runs"), ("GET", "/configs"), ("GET", "/datasets"),
                             ("GET", "/eval-runs"), ("GET", "/stats/overview")]:  # fmt: skip
            assert (await anon.request(method, API + path)).status_code == 401, (method, path)


async def test_api_key_is_stored_hashed_only(gw, sql) -> None:
    rows = sql("SELECT name, key_prefix, key_hash FROM api_keys")
    assert len(rows) == 1 and rows[0]["name"] == "admin"
    assert (
        rows[0]["key_prefix"] == KEY[:8]
        and rows[0]["key_hash"] != KEY
        and len(rows[0]["key_hash"]) == 64
    )
    assert KEY not in str(rows)


async def test_rotating_the_admin_key_revokes_the_old_one(gateway_factory) -> None:
    async with gateway_factory() as g1:
        assert (await g1.client.get(f"{API}/configs")).status_code == 200
    new_key = "a-brand-new-admin-key-0123456789"
    async with gateway_factory(admin_api_key=new_key) as g2:
        assert (
            await g2.client.get(f"{API}/configs")
        ).status_code == 401  # old key (client default header)
        ok = await g2.client.get(f"{API}/configs", headers={"X-API-Key": new_key})
        assert ok.status_code == 200


async def test_refuses_to_start_without_an_admin_key(gateway_factory) -> None:
    with pytest.raises(ValueError, match="ADMIN_API_KEY"):
        async with gateway_factory(admin_api_key=""):
            pass


async def test_cors_allows_the_dashboard_origin_only(gw) -> None:
    ok = await gw.client.options(
        f"{API}/runs",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "x-api-key,content-type"},
    )  # fmt: skip
    assert (
        ok.status_code == 200
        and ok.headers["access-control-allow-origin"] == "http://localhost:4200"
    )
    bad = await gw.client.options(
        f"{API}/runs",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


async def test_errors_are_problem_json(gw) -> None:
    for resp, status in [
        (await gw.client.get(f"{API}/nope"), 404),
        (await gw.client.get(f"{API}/runs/not-a-uuid"), 400),
        (await gw.client.get(f"{API}/runs/00000000-0000-4000-8000-000000000000"), 404),
        (await gw.client.post(f"{API}/runs", json={"question": ""}), 400),
    ]:
        assert resp.status_code == status, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["status"] == status and body["title"] and body["instance"].startswith("/")


# ------------------------------------------------------------------ configs


async def test_config_create_is_idempotent_on_content(gw) -> None:
    r1 = await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)
    assert r1.status_code == 201
    c1 = r1.json()
    assert c1["temperature"] == 0.0 and c1["max_steps"] == 12 and len(c1["config_hash"]) == 64

    r2 = await gw.client.post(
        f"{API}/configs", json={**INLINE_CONFIG, "name": "renamed", "tool_allowlist": ["calculate"]}
    )
    assert (
        r2.status_code == 200 and r2.json()["id"] == c1["id"] and r2.json()["name"] == "t"
    )  # the name is a label

    other = await gw.client.post(f"{API}/configs", json={**INLINE_CONFIG, "temperature": 0.7})
    assert other.status_code == 201 and other.json()["id"] != c1["id"]

    listed = (await gw.client.get(f"{API}/configs")).json()
    assert [c["id"] for c in listed] == [other.json()["id"], c1["id"]]  # newest first


@pytest.mark.parametrize(
    "bad",
    [{"temperature": 3}, {"max_steps": 0}, {"max_steps": 51}, {"max_cost_usd": 0}, {"max_cost_usd": -1}, {"name": ""},
     {"max_output_tokens": 10}],
)  # fmt: skip
async def test_config_validation(gw, bad) -> None:
    r = await gw.client.post(f"{API}/configs", json={**INLINE_CONFIG, **bad})
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")


async def test_inline_config_in_a_run_is_snapshotted_once(gw, sql) -> None:
    from gwutil import post_run

    await post_run(gw.client)
    await post_run(gw.client)
    assert sql("SELECT count(*) AS n FROM agent_configs")[0]["n"] == 1
