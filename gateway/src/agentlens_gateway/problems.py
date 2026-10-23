"""RFC 9457 `application/problem+json` errors, as promised by contracts/openapi.yaml."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("agentlens.gateway")
MEDIA_TYPE = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"{status} {title}: {detail}")
        self.status, self.title, self.detail, self.headers = status, title, detail, headers or {}


def not_found(what: str, ident: object) -> ProblemError:
    return ProblemError(404, "Not Found", f"{what} {ident} does not exist")


def bad_request(detail: str) -> ProblemError:
    return ProblemError(400, "Bad Request", detail)


def conflict(detail: str) -> ProblemError:
    return ProblemError(409, "Conflict", detail)


def _response(
    req: Request, status: int, title: str, detail: str | None, headers: dict[str, str] | None = None
):
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "instance": req.url.path,
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(req: Request, exc: ProblemError):
        return _response(req, exc.status, exc.title, exc.detail, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(req: Request, exc: StarletteHTTPException):
        title = {404: "Not Found", 405: "Method Not Allowed"}.get(exc.status_code, "Error")
        return _response(
            req, exc.status_code, title, str(exc.detail), getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(req: Request, exc: RequestValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        return _response(req, 400, "Bad Request", "; ".join(parts))

    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception):
        log.exception("unhandled error on %s %s", req.method, req.url.path)
        return _response(req, 500, "Internal Server Error", None)  # never leak internals
