from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

from coc_runner.error_details import build_structured_error_detail, shape_validation_error_items


def build_request_validation_detail(
    exc: RequestValidationError,
) -> dict[str, Any]:
    return build_structured_error_detail(
        code="request_validation_failed",
        message="请求参数校验失败",
        scope="request_validation",
        errors=shape_validation_error_items(exc.errors()),
    )


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(build_request_validation_detail(exc))},
    )
