from __future__ import annotations

from html import escape
from typing import Any, Callable
from urllib.parse import parse_qsl

from fastapi import Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from coc_runner.api.playtest_layout import render_playtest_shell
from coc_runner.domain.errors import ConflictError
from coc_runner.error_details import (
    build_structured_error_detail,
    extract_error_detail,
    shape_validation_error_items,
)


def _render_detail(detail: dict[str, Any] | str | None) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return (
            '<section class="feedback feedback-error">'
            "<h2>操作失败</h2>"
            f"<p>{escape(detail)}</p>"
            "</section>"
        )

    lines = [f"<p>{escape(detail.get('message', '操作失败'))}</p>"]
    code = detail.get("code")
    if code:
        lines.append(f'<p class="feedback-code">code: {escape(str(code))}</p>')
    for error in detail.get("errors", []):
        message = error.get("message")
        if message:
            lines.append(f"<li>{escape(str(message))}</li>")
    extra_list = "".join(line for line in lines[2:])
    return (
        '<section class="feedback feedback-error">'
        "<h2>操作失败</h2>"
        f"{lines[0]}"
        f"{lines[1] if len(lines) > 1 else ''}"
        f"{f'<ul>{extra_list}</ul>' if extra_list else ''}"
        "</section>"
    )


def _render_shell(
    *,
    title: str,
    body: str,
    status_code: int = status.HTTP_200_OK,
    include_form_script: bool = False,
) -> HTMLResponse:
    return render_playtest_shell(
        title=title,
        body=body,
        status_code=status_code,
        include_form_script=include_form_script,
    )


def _render_session_index_link() -> str:
    return '<a href="/playtest/sessions">返回 session 列表</a>'


def _render_knowledge_index_link(label: str = "查看准备资料") -> str:
    return f'<a href="/playtest/knowledge">{escape(label)}</a>'


def _render_session_create_link() -> str:
    return '<a href="/playtest/sessions/create">创建新局</a>'


def _format_datetime(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _normalize_form_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip()


async def _read_form_payload(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    return {key: value for key, value in parse_qsl(body, keep_blank_values=True)}


def _build_validation_detail(exc: ValidationError) -> dict[str, Any]:
    return build_structured_error_detail(
        code="request_validation_failed",
        message="请求参数校验失败",
        scope="request_validation",
        errors=shape_validation_error_items(exc.errors()),
    )


def _playtest_status_for_exception(exc: Exception) -> int:
    if isinstance(exc, ValidationError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if isinstance(exc, LookupError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, PermissionError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(exc, ConflictError):
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _render_playtest_exception(
    render_page: Callable[..., HTMLResponse],
    *,
    exc: ValidationError | LookupError | PermissionError | ConflictError | ValueError,
    **render_kwargs: Any,
) -> HTMLResponse:
    detail = (
        _build_validation_detail(exc)
        if isinstance(exc, ValidationError)
        else extract_error_detail(exc)
    )
    return render_page(
        detail=detail,
        status_code=_playtest_status_for_exception(exc),
        **render_kwargs,
    )
