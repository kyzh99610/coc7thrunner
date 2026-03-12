from __future__ import annotations

from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from coc_runner.api.exception_handlers import build_request_validation_detail
from tests.helpers import make_participant, make_scenario


def test_request_validation_body_missing_uses_structured_422_detail(
    client: TestClient,
) -> None:
    response = client.post(
        "/sessions/start",
        json={
            "scenario": {"title": "缺字段场景"},
            "participants": [],
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "request_validation_failed"
    assert detail["message"] == "请求参数校验失败"
    assert detail["scope"] == "request_validation"
    assert isinstance(detail["errors"], list)
    assert any(
        error["loc"] == ["body", "keeper_name"]
        and error["message"] == "Field required"
        and error["type"] == "missing"
        for error in detail["errors"]
    )
    assert any(
        error["loc"] == ["body", "scenario", "hook"]
        and error["type"] == "missing"
        for error in detail["errors"]
    )


def test_request_validation_body_type_errors_preserve_loc_message_type_and_input(
    client: TestClient,
) -> None:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": "oops",
            "participants": "oops",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    scenario_error = next(
        error for error in detail["errors"] if error["loc"] == ["body", "scenario"]
    )
    participants_error = next(
        error for error in detail["errors"] if error["loc"] == ["body", "participants"]
    )

    assert scenario_error["message"] == "Input should be a valid dictionary or object to extract fields from"
    assert scenario_error["type"] == "model_attributes_type"
    assert scenario_error["input"] == "oops"
    assert participants_error["message"] == "Input should be a valid list"
    assert participants_error["type"] == "list_type"
    assert participants_error["input"] == "oops"


def test_request_validation_query_errors_use_structured_422_detail(
    client: TestClient,
) -> None:
    response = client.get(
        "/sessions/session-probe/state",
        params={"viewer_role": "bad-role"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "request_validation_failed"
    assert detail["scope"] == "request_validation"
    assert detail["errors"] == [
        {
            "loc": ["query", "viewer_role"],
            "message": "Input should be 'keeper' or 'investigator'",
            "type": "enum",
            "input": "bad-role",
            "ctx": {"expected": "'keeper' or 'investigator'"},
        }
    ]


def test_request_validation_detail_builder_preserves_path_loc_entries() -> None:
    detail = build_request_validation_detail(
        RequestValidationError(
            [
                {
                    "type": "enum",
                    "loc": ("path", "session_id"),
                    "msg": "Input should be a valid UUID",
                    "input": "not-a-uuid",
                }
            ]
        )
    )

    assert detail == {
        "code": "request_validation_failed",
        "message": "请求参数校验失败",
        "scope": "request_validation",
        "errors": [
            {
                "loc": ["path", "session_id"],
                "message": "Input should be a valid UUID",
                "type": "enum",
                "input": "not-a-uuid",
            }
        ],
    }


def test_structured_business_errors_are_not_changed_by_global_422_handler(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "missing-character-import-source",
            "sync_policy": "refresh_with_merge",
        },
    )

    assert apply_response.status_code == 404
    assert apply_response.json()["detail"] == {
        "code": "character_import_source_not_found",
        "message": "未找到角色导入源 missing-character-import-source",
        "source_id": "missing-character-import-source",
        "session_id": session_id,
        "actor_id": "investigator-1",
        "scope": "character_import_source",
    }
