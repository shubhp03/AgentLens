from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from agentlens_gateway.schemas import AgentConfigCreate
from agentlens_gateway.security import authenticate

router = APIRouter(dependencies=[Depends(authenticate)])


@router.post("/configs")
async def create_config(body: AgentConfigCreate, request: Request):
    """Immutable snapshot, idempotent on config_hash: 201 when created, 200 with the existing row otherwise."""
    config, created = await request.app.state.store.create_config(body.model_dump())
    return JSONResponse(jsonable_encoder(config), status_code=201 if created else 200)


@router.get("/configs")
async def list_configs(request: Request):
    return await request.app.state.store.list_configs()
