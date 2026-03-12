from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request


def build_request_validation_detail(
    exc: RequestValidationError,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        shaped_error: dict[str, Any] = {
            "loc": list(error.get("loc", ())),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        if "input" in error:
            shaped_error["input"] = error["input"]
        if "ctx" in error:
            shaped_error["ctx"] = error["ctx"]
        errors.append(shaped_error)
    return {
        "code": "request_validation_failed",
        "message": "请求参数校验失败",
        "scope": "request_validation",
        "errors": errors,
    }


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(build_request_validation_detail(exc))},
    )
