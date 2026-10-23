from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from agentlens_gateway.schemas import DatasetCreate
from agentlens_gateway.security import authenticate

router = APIRouter(dependencies=[Depends(authenticate)])


@router.post("/datasets")
async def upsert_dataset(body: DatasetCreate, request: Request):
    """201 when created, 200 when the identical dataset (name+version+content_hash) is already registered,
    409 when name+version exist with different content (datasets are immutable once published)."""
    ds, outcome = await request.app.state.store.upsert_dataset(body.model_dump())
    return JSONResponse(jsonable_encoder(ds), status_code=201 if outcome == "created" else 200)


@router.get("/datasets")
async def list_datasets(request: Request):
    return await request.app.state.store.list_datasets()


@router.get("/datasets/{dataset_id}/cases")
async def list_cases(dataset_id: UUID, request: Request, category: str | None = None):
    return await request.app.state.store.list_cases(dataset_id, category)
