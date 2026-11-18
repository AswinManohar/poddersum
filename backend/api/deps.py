"""Shared route dependencies and the API error type."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import ErrorResponse
from db.session import get_session


class ApiError(Exception):
    """Raise from a route to return an :class:`ErrorResponse` with ``status``."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def error_response(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ErrorResponse(detail=detail, code=code).model_dump()
    )


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status, exc.code, exc.detail)


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The app-wide outbound HTTP client (created in the lifespan)."""
    return request.app.state.http_client


def errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses=`` entries documenting :class:`ErrorResponse` bodies."""
    descriptions = {
        404: "Unknown id",
        409: "Conflict",
        502: "Upstream (iTunes or feed host) failed",
    }
    return {
        s: {"model": ErrorResponse, "description": descriptions.get(s, "Error")} for s in statuses
    }


SessionDep = Annotated[AsyncSession, Depends(get_session)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
