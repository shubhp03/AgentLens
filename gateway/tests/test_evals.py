"""Datasets, eval runs, results, case-level metrics, baseline, compare, overview."""

from __future__ import annotations

from uuid import uuid4

import pytest
from agentlens_evals.stats import mcnemar_exact, wilson_interval
from gwutil import INLINE_CONFIG, post_run, wait_for

API = "/api/v1"


def case(key: str, category: str = "lookup", **kw):
    return {"case_key": key, "question": f"question for {key}", "category": category, "difficulty": "easy",
            "expected": {"kind": "numeric", "value": 1.0, "unit": "USD"}, "required_tools": ["get_financial_facts"],
            "provenance": {"source": "test"}, "status": "verified", "tags": ["t"], **kw}  # fmt: skip


async def make_dataset(
    client, keys=("cs1", "cs2"), categories=None, version="v1", content_hash="h1"
):
    categories = categories or {}
    body = {"name": "golden", "version": version, "description": "d", "content_hash": content_hash,
            "cases": [case(k, categories.get(k, "lookup")) for k in keys]}  # fmt: skip
    return await client.post(f"{API}/datasets", json=body)


async def make_run(client) -> str:
    rid = (await post_run(client)).json()["id"]
    await wait_for(client, rid, "completed")
    return rid


def result(case_key, run_id, passed, repeat=0, category=None):
    return {"case_key": case_key, "repeat_idx": repeat, "run_id": run_id, "passed": passed,
            "failure_category": None if passed else (category or "calc_error"), "scores": {"task_success": passed}}  # fmt: skip


# ------------------------------------------------------------------ datasets


async def test_dataset_create_idempotent_and_immutable(gw) -> None:
    first = await make_dataset(gw.client)
    assert first.status_code == 201
    ds = first.json()
    assert ds["case_count"] == 2 and ds["name"] == "golden" and ds["content_hash"] == "h1"

    again = await make_dataset(gw.client)
    assert again.status_code == 200 and again.json()["id"] == ds["id"]  # identical content: no-op

    changed = await make_dataset(gw.client, keys=("cs1", "cs2", "cs3"), content_hash="h2")
    assert changed.status_code == 409 and "bump the version" in changed.json()["detail"]
    assert (
        await make_dataset(gw.client, keys=("cs1", "cs2", "cs3"), version="v2", content_hash="h2")
    ).status_code == 201
    assert len((await gw.client.get(f"{API}/datasets")).json()) == 2


async def test_dataset_validation_and_case_listing(gw) -> None:
    dup = await gw.client.post(
        f"{API}/datasets",
        json={
            "name": "g",
            "version": "v",
            "content_hash": "h",
            "cases": [case("aaa"), case("aaa")],
        },
    )
    assert dup.status_code == 400 and "duplicate" in dup.json()["detail"]
    for bad in ({"case_key": "X"}, {"category": "nope"}, {"difficulty": "extreme"}):
        r = await gw.client.post(
            f"{API}/datasets",
            json={
                "name": "g",
                "version": "v",
                "content_hash": "h",
                "cases": [{**case("aaa"), **bad}],
            },
        )
        assert r.status_code == 400
    empty = await gw.client.post(
        f"{API}/datasets", json={"name": "g", "version": "v", "content_hash": "h", "cases": []}
    )
    assert empty.status_code == 400

    ds = (
        await make_dataset(gw.client, keys=("cs1", "cs2", "cs3"), categories={"cs2": "calc"})
    ).json()
    allc = (await gw.client.get(f"{API}/datasets/{ds['id']}/cases")).json()
    assert [c["case_key"] for c in allc] == ["cs1", "cs2", "cs3"]
    assert allc[0]["expected"] == {"kind": "numeric", "value": 1.0, "unit": "USD"} and allc[0][
        "tags"
    ] == ["t"]
    only = (
        await gw.client.get(f"{API}/datasets/{ds['id']}/cases", params={"category": "calc"})
    ).json()
    assert [c["case_key"] for c in only] == ["cs2"]
    assert (await gw.client.get(f"{API}/datasets/{uuid4()}/cases")).status_code == 404


# ------------------------------------------------------------------ eval runs + results + metrics


async def setup_eval(gw, keys=("cs1", "cs2"), categories=None):
    ds = (await make_dataset(gw.client, keys, categories)).json()
    cfg = (await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)).json()
    er = await gw.client.post(
        f"{API}/eval-runs",
        json={"dataset_id": ds["id"], "config_id": cfg["id"], "label": "t", "repeats": 3},
    )
    assert er.status_code == 201
    return ds, cfg, er.json()


async def test_eval_run_create_validation(gw) -> None:
    ds, cfg, er = await setup_eval(gw)
    assert (
        er["status"] == "running"
        and er["repeats"] == 3
        and er["metrics"] is None
        and er["is_baseline"] is False
    )
    assert (
        await gw.client.post(
            f"{API}/eval-runs", json={"dataset_id": str(uuid4()), "config_id": cfg["id"]}
        )
    ).status_code == 404
    assert (
        await gw.client.post(
            f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": str(uuid4())}
        )
    ).status_code == 404
    assert (
        await gw.client.post(
            f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": cfg["id"], "repeats": 11}
        )
    ).status_code == 400
    assert (await gw.client.get(f"{API}/eval-runs/{uuid4()}")).status_code == 404


async def test_case_level_metrics_failure_breakdown_and_idempotent_results(gw, sql) -> None:
    ds, cfg, er = await setup_eval(gw, keys=("cs1", "cs2"), categories={"cs2": "calc"})
    run_id = await make_run(gw.client)
    # c1: 1 of 2 repeats pass -> NOT a majority -> the case FAILS.  c2: 2 of 3 pass -> the case passes.
    batch = [result("cs1", run_id, True, 0), result("cs1", run_id, False, 1, "wrong_period"),
             result("cs2", run_id, True, 0), result("cs2", run_id, True, 1), result("cs2", run_id, False, 2, "calc_error")]  # fmt: skip
    r = await gw.client.post(f"{API}/eval-runs/{er['id']}/results", json=batch)
    assert r.status_code == 204 and r.content == b""
    assert (
        await gw.client.post(f"{API}/eval-runs/{er['id']}/results", json=batch)
    ).status_code == 204  # replay
    assert sql("SELECT count(*) AS n FROM eval_results")[0]["n"] == 5  # idempotent

    detail = (await gw.client.get(f"{API}/eval-runs/{er['id']}")).json()
    m = detail["metrics"]
    assert (m["n_results"], m["n_cases"], m["n_cases_passed"]) == (5, 2, 1)
    assert m["pass_rate"] == 0.5 and m["run_pass_rate"] == pytest.approx(
        0.6
    )  # headline is CASE-level
    assert m["pass_rate_ci"] == pytest.approx(list(wilson_interval(1, 2)))
    assert m["total_cost_usd"] == pytest.approx(0.10) and m[
        "cost_per_success_usd"
    ] == pytest.approx(0.10 / 3)
    assert m["p50_latency_ms"] == 900
    assert detail["failure_breakdown"] == {"calc_error": 1, "wrong_period": 1}
    assert detail["category_pass_rates"] == {"calc": 1.0, "lookup": 0.0}

    # results can be filtered; the run's cost/latency were copied from the stored run
    allr = (await gw.client.get(f"{API}/eval-runs/{er['id']}/results")).json()
    assert [(x["case_key"], x["repeat_idx"]) for x in allr] == [
        ("cs1", 0),
        ("cs1", 1),
        ("cs2", 0),
        ("cs2", 1),
        ("cs2", 2),
    ]
    assert (
        allr[0]["cost_usd"] == pytest.approx(0.02)
        and allr[0]["latency_ms"] == 900
        and allr[0]["scores"] == {"task_success": True}
    )
    failed = (
        await gw.client.get(f"{API}/eval-runs/{er['id']}/results", params={"passed": "false"})
    ).json()
    assert {(x["case_key"], x["failure_category"]) for x in failed} == {
        ("cs1", "wrong_period"),
        ("cs2", "calc_error"),
    }
    by_cat = (
        await gw.client.get(f"{API}/eval-runs/{er['id']}/results", params={"category": "calc"})
    ).json()
    assert {x["case_key"] for x in by_cat} == {"cs2"}
    by_fc = (
        await gw.client.get(
            f"{API}/eval-runs/{er['id']}/results", params={"failure_category": "calc_error"}
        )
    ).json()
    assert len(by_fc) == 1

    listed = (await gw.client.get(f"{API}/eval-runs", params={"dataset_id": ds["id"]})).json()
    assert listed[0]["id"] == er["id"] and listed[0]["metrics"]["n_cases"] == 2


async def test_result_posting_rejects_bad_input_atomically(gw, sql) -> None:
    _, _, er = await setup_eval(gw)
    run_id = await make_run(gw.client)
    url = f"{API}/eval-runs/{er['id']}/results"
    good = result("cs1", run_id, True)
    cases = [
        ([{**result("cs1", run_id, False), "failure_category": None}], "needs a failure_category"),
        ([{**result("cs1", run_id, True), "failure_category": "calc_error"}], "must not have"),
        ([result("cs1", run_id, False, category="made_up")], "unknown failure_category"),
        ([result("nope", run_id, True)], "case_key not in this eval run"),
        ([result("cs1", str(uuid4()), True)], "unknown run_id"),
        ([], "between 1 and 200"),
        ([result("cs1", run_id, True, repeat=i) for i in range(201)], "between 1 and 200"),
    ]
    for body, fragment in cases:
        r = await gw.client.post(url, json=body)
        assert r.status_code == 400 and fragment in r.json()["detail"], (fragment, r.text)
    # a batch with one bad item stores nothing at all
    mixed = await gw.client.post(url, json=[good, result("nope", run_id, True)])
    assert mixed.status_code == 400
    assert sql("SELECT count(*) AS n FROM eval_results")[0]["n"] == 0
    assert (
        await gw.client.post(f"{API}/eval-runs/{uuid4()}/results", json=[good])
    ).status_code == 404
    assert (await gw.client.get(f"{API}/eval-runs/{uuid4()}/results")).status_code == 404


async def test_complete_and_baseline_semantics(gw) -> None:
    ds, cfg, a = await setup_eval(gw)
    b = (
        await gw.client.post(
            f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": cfg["id"]}
        )
    ).json()

    done = await gw.client.post(
        f"{API}/eval-runs/{a['id']}/complete", json={"status": "completed", "gate_passed": True}
    )
    assert (
        done.status_code == 200
        and done.json()["status"] == "completed"
        and done.json()["gate_passed"] is True
    )
    assert done.json()["finished_at"] is not None
    assert (
        await gw.client.post(f"{API}/eval-runs/{a['id']}/complete", json={"status": "failed"})
    ).status_code == 409
    assert (
        await gw.client.post(f"{API}/eval-runs/{a['id']}/complete", json={"status": "bogus"})
    ).status_code == 400
    assert (
        await gw.client.post(f"{API}/eval-runs/{uuid4()}/complete", json={"status": "completed"})
    ).status_code == 404

    p1 = await gw.client.patch(
        f"{API}/eval-runs/{a['id']}", json={"is_baseline": True, "notes": "first baseline"}
    )
    assert (
        p1.status_code == 200
        and p1.json()["is_baseline"] is True
        and p1.json()["notes"] == "first baseline"
    )
    p2 = await gw.client.patch(
        f"{API}/eval-runs/{b['id']}", json={"is_baseline": True}
    )  # promotes B, demotes A
    assert p2.json()["is_baseline"] is True
    after = {r["id"]: r for r in (await gw.client.get(f"{API}/eval-runs")).json()}
    assert after[a["id"]]["is_baseline"] is False and after[b["id"]]["is_baseline"] is True
    assert after[a["id"]]["notes"] == "first baseline"  # untouched fields are preserved
    assert (
        await gw.client.patch(f"{API}/eval-runs/{uuid4()}", json={"notes": "x"})
    ).status_code == 404


# ------------------------------------------------------------------ compare


async def _eval_with_outcomes(gw, ds, cfg, outcomes: dict[str, bool]) -> str:
    er = (
        await gw.client.post(
            f"{API}/eval-runs", json={"dataset_id": ds["id"], "config_id": cfg["id"]}
        )
    ).json()
    run_id = await make_run(gw.client)
    await gw.client.post(
        f"{API}/eval-runs/{er['id']}/results",
        json=[result(k, run_id, v) for k, v in outcomes.items()],
    )
    return er["id"]


async def test_compare_uses_case_level_outcomes_and_the_shared_statistics(gw) -> None:
    keys = tuple(f"case{i}" for i in range(10))
    ds = (await make_dataset(gw.client, keys)).json()
    cfg = (await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)).json()
    base = await _eval_with_outcomes(gw, ds, cfg, {k: True for k in keys})
    cand = await _eval_with_outcomes(
        gw, ds, cfg, {k: i >= 8 for i, k in enumerate(keys)}
    )  # 8 regress, 2 stay passing

    r = await gw.client.get(f"{API}/eval-runs/compare", params={"base": base, "candidate": cand})
    assert r.status_code == 200
    c = r.json()
    assert c["n"] == 10 and c["base_pass_rate"] == 1.0 and c["candidate_pass_rate"] == 0.2
    assert c["delta"] == pytest.approx(-0.8) and (c["base_only"], c["candidate_only"]) == (8, 0)
    assert c["p_value"] == pytest.approx(mcnemar_exact(8, 0)) and c["regression"] is True
    assert c["base_ci"] == pytest.approx(list(wilson_interval(10, 10)))
    assert c["candidate_ci"] == pytest.approx(list(wilson_interval(2, 10)))
    assert c["flipped_cases"] == [
        {"case_key": f"case{i}", "direction": "regressed"} for i in range(8)
    ]

    rev = (
        await gw.client.get(f"{API}/eval-runs/compare", params={"base": cand, "candidate": base})
    ).json()
    assert rev["regression"] is False and rev["flipped_cases"][0]["direction"] == "improved"


async def test_compare_errors(gw) -> None:
    ds = (await make_dataset(gw.client, ("cs1",))).json()
    ds2 = (await make_dataset(gw.client, ("zz1",), version="v2", content_hash="h2")).json()
    cfg = (await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)).json()
    a = await _eval_with_outcomes(gw, ds, cfg, {"cs1": True})
    b = await _eval_with_outcomes(gw, ds2, cfg, {"zz1": True})
    assert (
        await gw.client.get(f"{API}/eval-runs/compare", params={"base": a, "candidate": b})
    ).status_code == 400  # no shared cases
    assert (
        await gw.client.get(
            f"{API}/eval-runs/compare", params={"base": a, "candidate": str(uuid4())}
        )
    ).status_code == 404
    assert (
        await gw.client.get(f"{API}/eval-runs/compare", params={"base": a})
    ).status_code == 400  # missing param


# ------------------------------------------------------------------ overview


async def test_overview_reports_spend_and_latest_baseline(gw) -> None:
    empty = (await gw.client.get(f"{API}/stats/overview")).json()
    assert empty == {
        "runs_today": 0,
        "cost_today_usd": 0.0,
        "daily_cap_usd": 100.0,
        "latest_baseline_pass_rate": None,
    }

    ds, cfg, er = await setup_eval(gw)
    run_id = await make_run(gw.client)
    await gw.client.post(
        f"{API}/eval-runs/{er['id']}/results",
        json=[result("cs1", run_id, True), result("cs2", run_id, False)],
    )
    await gw.client.patch(f"{API}/eval-runs/{er['id']}", json={"is_baseline": True})
    ov = (await gw.client.get(f"{API}/stats/overview")).json()
    assert (
        ov["runs_today"] == 1
        and ov["cost_today_usd"] == pytest.approx(0.02)
        and ov["latest_baseline_pass_rate"] == 0.5
    )
