from __future__ import annotations

from typing import Any


def build_structured_error_detail(
    *,
    code: str,
    message: str,
    scope: str,
    **extra_fields: Any,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "code": code,
        "message": message,
        "scope": scope,
    }
    for key, value in extra_fields.items():
        if value is not None:
            detail[key] = value
    return detail


def shape_validation_error_items(
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    shaped_errors: list[dict[str, Any]] = []
    for error in errors:
        shaped_error: dict[str, Any] = {
            "loc": list(error.get("loc", ())),
            "message": error.get("msg", error.get("message", "")),
            "type": error.get("type", ""),
        }
        if "input" in error:
            shaped_error["input"] = error["input"]
        if "ctx" in error:
            shaped_error["ctx"] = error["ctx"]
        shaped_errors.append(shaped_error)
    return shaped_errors


def build_character_import_error_detail(
    *,
    code: str,
    message: str,
    scope: str,
    source_id: str,
    session_id: str,
    actor_id: str,
    **extra_fields: Any,
) -> dict[str, Any]:
    return build_structured_error_detail(
        code=code,
        message=message,
        scope=scope,
        source_id=source_id,
        session_id=session_id,
        actor_id=actor_id,
        **extra_fields,
    )


def build_session_action_error_detail(
    *,
    code: str,
    message: str,
    scope: str,
    session_id: str,
    actor_id: str | None = None,
    operator_id: str | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    return build_structured_error_detail(
        code=code,
        message=message,
        scope=scope,
        session_id=session_id,
        actor_id=actor_id,
        operator_id=operator_id,
        **extra_fields,
    )


def extract_error_detail(exc: BaseException) -> Any:
    return exc.args[0] if exc.args and isinstance(exc.args[0], dict) else str(exc)
