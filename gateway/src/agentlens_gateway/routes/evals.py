"""Eval runs, results, comparison and the dashboard overview."""

from __future__ import annotations

from uuid import UUID

from agentlens_evals.compare import compare_case_outcomes
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from agentlens_gateway.problems import bad_request
from agentlens_gateway.schemas import (
    EvalResultCreate,
    EvalRunComplete,
    EvalRunCreate,
    EvalRunUpdate,
)
from agentlens_gateway.security import authenticate
from agentlens_gateway.store import flipped_cases

router = APIRouter(dependencies=[Depends(authenticate)])


@router.post("/eval-runs", status_code=201)
async def create_eval_run(body: EvalRunCreate, request: Request):
    return await request.app.state.store.create_eval_run(body.model_dump())


@router.get("/eval-runs")
async def list_eval_runs(
    request: Request, dataset_id: UUID | None = None, limit: int = Query(50, ge=1, le=200)
):
    return await request.app.state.store.list_eval_runs(dataset_id, limit)


# NOTE: declared before /eval-runs/{eval_run_id}; otherwise "compare" would be parsed as an id.
@router.get("/eval-runs/compare")
async def compare_eval_runs(request: Request, base: UUID, candidate: UUID):
    store = request.app.state.store
    b, c = await store.case_outcomes(base), await store.case_outcomes(candidate)
    try:
        cmp = compare_case_outcomes(
            b, c
        )  # the same code the CLI and CI gate use: one implementation of the statistics
    except ValueError as e:
        raise bad_request(str(e)) from e
    return {
        "n": cmp.n,
        "base_pass_rate": cmp.base_pass_rate,
        "candidate_pass_rate": cmp.cand_pass_rate,
        "base_ci": list(cmp.base_ci),
        "candidate_ci": list(cmp.cand_ci),
        "delta": cmp.delta,
        "base_only": cmp.base_only,
        "candidate_only": cmp.cand_only,
        "p_value": cmp.p_value,
        "regression": cmp.is_regression(),
        "flipped_cases": flipped_cases(b, c),
    }


@router.get("/eval-runs/{eval_run_id}")
async def get_eval_run(eval_run_id: UUID, request: Request):
    return await request.app.state.store.get_eval_run_detail(eval_run_id)


@router.patch("/eval-runs/{eval_run_id}")
async def update_eval_run(eval_run_id: UUID, body: EvalRunUpdate, request: Request):
    return await request.app.state.store.update_eval_run(
        eval_run_id, is_baseline=body.is_baseline, notes=body.notes
    )


@router.post("/eval-runs/{eval_run_id}/results", status_code=204)
async def add_results(eval_run_id: UUID, body: list[EvalResultCreate], request: Request):
    if not 1 <= len(body) <= 200:
        raise bad_request("send between 1 and 200 results per request")
    await request.app.state.store.add_results(eval_run_id, [r.model_dump() for r in body])
    return Response(status_code=204)


@router.get("/eval-runs/{eval_run_id}/results")
async def list_results(
    eval_run_id: UUID,
    request: Request,
    passed: bool | None = None,
    failure_category: str | None = None,
    category: str | None = None,
):
    return await request.app.state.store.list_results(
        eval_run_id, passed=passed, failure_category=failure_category, category=category
    )


@router.post("/eval-runs/{eval_run_id}/complete")
async def complete_eval_run(eval_run_id: UUID, body: EvalRunComplete, request: Request):
    return await request.app.state.store.complete_eval_run(
        eval_run_id, body.status, body.gate_passed
    )


@router.get("/stats/overview")
async def overview(request: Request):
    out = await request.app.state.store.overview()
    out["daily_cap_usd"] = request.app.state.settings.daily_cost_cap_usd
    return JSONResponse(jsonable_encoder(out))
