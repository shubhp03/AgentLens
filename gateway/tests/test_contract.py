"""GW-20: the running gateway conforms to contracts/openapi.yaml.

1. Every operation in the contract exists in the app (and the app exposes nothing undocumented).
2. Real responses, success and error, validate against the schema the contract declares for that operation/status.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from gwutil import INLINE_CONFIG, post_run, wait_for
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from test_evals import case, result

SPEC_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SPEC = yaml.safe_load(SPEC_PATH.read_text())
REGISTRY = Registry().with_resource(
    "urn:openapi", Resource.from_contents(SPEC, default_specification=DRAFT202012)
)
METHODS = {"get", "post", "put", "patch", "delete"}
API = "/api/v1"


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target: Any = SPEC
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target[part]
        node = target
    return node


def check(method: str, template: str, resp, *, status: int | None = None) -> None:
    """Validate `resp` against the contract for (method, path template, status)."""
    status = status or resp.status_code
    assert resp.status_code == status, resp.text
    op = SPEC["paths"][template][method.lower()]
    declared = op["responses"].get(str(status))
    assert declared is not None, (
        f"{method} {template}: status {status} is not declared in the contract"
    )
    content = _resolve(declared).get("content")
    if not content:
        assert not resp.content, (
            f"{method} {template} {status}: contract declares no body but got {resp.text[:80]!r}"
        )
        return
    ctype = resp.headers["content-type"].split(";")[0]
    assert ctype in content, f"{method} {template} {status}: undeclared content type {ctype}"
    schema = content[ctype]["schema"]
    if "$ref" in schema:
        schema = {"$ref": "urn:openapi" + schema["$ref"]}
    else:
        schema = {
            **schema,
            "components": SPEC["components"],
        }  # inline schema that itself contains $refs
        schema = _with_abs_refs(schema)
    errors = list(
        Draft202012Validator(schema, registry=REGISTRY, format_checker=FormatChecker()).iter_errors(
            resp.json()
        )
    )
    assert not errors, f"{method} {template} {status} violates the contract: " + "; ".join(
        e.message for e in errors[:3]
    )
    for name in declared.get("headers") or {}:
        assert name.lower() in {k.lower() for k in resp.headers}, (
            f"{method} {template} {status}: declared header {name} missing"
        )


def _with_abs_refs(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: (
                "urn:openapi" + v
                if k == "$ref" and isinstance(v, str) and v.startswith("#")
                else _with_abs_refs(v)
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_with_abs_refs(x) for x in node]
    return node


# ------------------------------------------------------------------ 1. coverage


def test_every_contract_operation_is_implemented_and_nothing_is_undocumented(gw) -> None:
    spec_ops = {
        (m.upper(), _norm(p)) for p, item in SPEC["paths"].items() for m in item if m in METHODS
    }
    app_ops = {
        (m.upper(), _norm(p))
        for p, item in gw.app.openapi()["paths"].items()
        for m in item
        if m in METHODS
    }
    app_ops.add(
        ("GET", "/healthz")
    )  # deliberately hidden from FastAPI's own schema; the contract documents it
    assert spec_ops - app_ops == set(), (
        f"in the contract but not implemented: {sorted(spec_ops - app_ops)}"
    )
    assert app_ops - spec_ops == set(), (
        f"implemented but not in the contract: {sorted(app_ops - spec_ops)}"
    )


# ------------------------------------------------------------------ 2. responses


async def test_runs_and_configs_responses_match_the_contract(gw) -> None:
    c = gw.client
    check("GET", "/healthz", await c.get("/healthz"))

    cfg = await c.post(f"{API}/configs", json=INLINE_CONFIG)
    check("POST", f"{API}/configs", cfg, status=201)
    check("POST", f"{API}/configs", await c.post(f"{API}/configs", json=INLINE_CONFIG), status=200)
    check("GET", f"{API}/configs", await c.get(f"{API}/configs"))

    created = await post_run(c)
    check("POST", f"{API}/runs", created, status=202)
    rid = created.json()["id"]
    await wait_for(c, rid, "completed")
    check("GET", f"{API}/runs", await c.get(f"{API}/runs", params={"limit": 1}))
    check("GET", f"{API}/runs/{{runId}}", await c.get(f"{API}/runs/{rid}"))
    check(
        "GET",
        f"{API}/runs/{{runId}}",
        await c.get(f"{API}/runs/{rid}", params={"include": "spans"}),
    )
    check("GET", f"{API}/runs/{{runId}}", await c.get(f"{API}/runs/{uuid4()}"), status=404)
    check("DELETE", f"{API}/runs/{{runId}}", await c.delete(f"{API}/runs/{rid}"), status=409)
    check("DELETE", f"{API}/runs/{{runId}}", await c.delete(f"{API}/runs/{uuid4()}"), status=404)
    check("POST", f"{API}/runs", await c.post(f"{API}/runs", json={"question": ""}), status=400)


async def test_cancel_and_rate_limit_responses_match_the_contract(gateway_factory) -> None:
    from gwutil import FakeAgent

    agent = FakeAgent()
    agent.mode = "block"
    async with gateway_factory(agent, default_rate_limit_per_min=2) as gw:
        rid = (await post_run(gw.client)).json()["id"]
        await agent.first_span_sent.wait()
        check(
            "DELETE",
            f"{API}/runs/{{runId}}",
            await gw.client.delete(f"{API}/runs/{rid}"),
            status=202,
        )
        await wait_for(gw.client, rid, "cancelled")
        await post_run(gw.client)
        check(
            "POST", f"{API}/runs", await post_run(gw.client), status=429
        )  # includes the Retry-After header


async def test_eval_responses_match_the_contract(gw) -> None:
    c = gw.client
    body = {
        "name": "golden",
        "version": "v1",
        "content_hash": "h1",
        "cases": [case("cs1"), case("cs2", "calc")],
    }
    check("POST", f"{API}/datasets", await c.post(f"{API}/datasets", json=body), status=201)
    check("POST", f"{API}/datasets", await c.post(f"{API}/datasets", json=body), status=200)
    check(
        "POST",
        f"{API}/datasets",
        await c.post(f"{API}/datasets", json={**body, "content_hash": "other"}),
        status=409,
    )
    check("GET", f"{API}/datasets", await c.get(f"{API}/datasets"))
    ds_id = (await c.get(f"{API}/datasets")).json()[0]["id"]
    check(
        "GET", f"{API}/datasets/{{datasetId}}/cases", await c.get(f"{API}/datasets/{ds_id}/cases")
    )

    ds, cfg, er = await setup_eval_again(gw)
    check("GET", f"{API}/eval-runs", await c.get(f"{API}/eval-runs"))
    check(
        "GET", f"{API}/eval-runs/{{evalRunId}}", await c.get(f"{API}/eval-runs/{er['id']}")
    )  # no metrics yet

    run_id = (await post_run(c)).json()["id"]
    await wait_for(c, run_id, "completed")
    posted = await c.post(
        f"{API}/eval-runs/{er['id']}/results",
        json=[result("cs1", run_id, True), result("cs2", run_id, False)],
    )
    check("POST", f"{API}/eval-runs/{{evalRunId}}/results", posted, status=204)
    check(
        "POST",
        f"{API}/eval-runs/{{evalRunId}}/results",
        await c.post(f"{API}/eval-runs/{er['id']}/results", json=[]),
        status=400,
    )
    check(
        "GET",
        f"{API}/eval-runs/{{evalRunId}}/results",
        await c.get(f"{API}/eval-runs/{er['id']}/results"),
    )
    check(
        "GET", f"{API}/eval-runs/{{evalRunId}}", await c.get(f"{API}/eval-runs/{er['id']}")
    )  # with metrics + breakdown
    check(
        "GET",
        f"{API}/eval-runs/{{evalRunId}}",
        await c.get(f"{API}/eval-runs/{uuid4()}"),
        status=404,
    )
    check(
        "PATCH",
        f"{API}/eval-runs/{{evalRunId}}",
        await c.patch(f"{API}/eval-runs/{er['id']}", json={"is_baseline": True}),
    )
    check(
        "POST",
        f"{API}/eval-runs/{{evalRunId}}/complete",
        await c.post(
            f"{API}/eval-runs/{er['id']}/complete",
            json={"status": "completed", "gate_passed": False},
        ),
    )

    er2 = (
        await c.post(f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": cfg["id"]})
    ).json()
    check(
        "POST",
        f"{API}/eval-runs",
        await c.post(f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": cfg["id"]}),
        status=201,
    )
    await c.post(
        f"{API}/eval-runs/{er2['id']}/results",
        json=[result("cs1", run_id, True), result("cs2", run_id, True)],
    )
    check(
        "GET",
        f"{API}/eval-runs/compare",
        await c.get(f"{API}/eval-runs/compare", params={"base": er["id"], "candidate": er2["id"]}),
    )
    check("GET", f"{API}/stats/overview", await c.get(f"{API}/stats/overview"))


async def setup_eval_again(gw):
    """Like test_evals.setup_eval but on the dataset created by the caller (name golden@v1)."""
    ds = (await gw.client.get(f"{API}/datasets")).json()[0]
    cfg = (await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)).json()
    er = (
        await gw.client.post(
            f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": cfg["id"], "label": "t"}
        )
    ).json()
    return ds, cfg, er


async def test_auth_failures_are_contract_problems(gw) -> None:
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw.app), base_url="http://test"
    ) as anon:
        r = await anon.get(f"{API}/runs")
    assert r.status_code == 401
    assert Draft202012Validator(
        {"$ref": "urn:openapi#/components/schemas/Problem"}, registry=REGISTRY
    ).is_valid(r.json())
