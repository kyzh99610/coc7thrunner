from __future__ import annotations

from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from coc_runner.api.exception_handlers import build_request_validation_detail
from coc_runner.error_details import (
    build_character_import_error_detail,
    build_rules_query_error_detail,
    build_session_action_error_detail,
    shape_validation_error_items,
    build_structured_error_detail,
    extract_error_detail,
)
from tests.helpers import make_participant, make_scenario


def test_structured_error_helper_builds_expected_business_detail_shape() -> None:
    assert build_character_import_error_detail(
        code="character_import_source_not_found",
        message="未找到角色导入源 missing-source",
        scope="character_import_source",
        source_id="missing-source",
        session_id="session-123",
        actor_id="investigator-1",
    ) == {
        "code": "character_import_source_not_found",
        "message": "未找到角色导入源 missing-source",
        "scope": "character_import_source",
        "source_id": "missing-source",
        "session_id": "session-123",
        "actor_id": "investigator-1",
    }
    assert build_session_action_error_detail(
        code="manual_action_state_conflict",
        message="会话状态版本冲突，请重新加载后再试",
        scope="manual_action_state",
        session_id="session-123",
        actor_id="keeper-1",
        operator_id="keeper-1",
    ) == {
        "code": "manual_action_state_conflict",
        "message": "会话状态版本冲突，请重新加载后再试",
        "scope": "manual_action_state",
        "session_id": "session-123",
        "actor_id": "keeper-1",
        "operator_id": "keeper-1",
    }
    assert build_session_action_error_detail(
        code="player_action_invalid",
        message="actor_id investigator-missing 不属于当前会话",
        scope="player_action_request",
        session_id="session-456",
        actor_id="investigator-missing",
    ) == {
        "code": "player_action_invalid",
        "message": "actor_id investigator-missing 不属于当前会话",
        "scope": "player_action_request",
        "session_id": "session-456",
        "actor_id": "investigator-missing",
    }
    assert build_session_action_error_detail(
        code="draft_review_conflict",
        message="草稿 draft-123 已过期，当前版本 5 与草稿版本 1 差距过大",
        scope="draft_review_state",
        session_id="session-789",
        actor_id="ai-1",
        draft_id="draft-123",
        reviewer_id="keeper-1",
    ) == {
        "code": "draft_review_conflict",
        "message": "草稿 draft-123 已过期，当前版本 5 与草稿版本 1 差距过大",
        "scope": "draft_review_state",
        "session_id": "session-789",
        "actor_id": "ai-1",
        "draft_id": "draft-123",
        "reviewer_id": "keeper-1",
    }
    assert build_session_action_error_detail(
        code="keeper_prompt_not_found",
        message="未找到 KP 提示 prompt-123",
        scope="keeper_prompt_prompt",
        session_id="session-321",
        operator_id="keeper-1",
        prompt_id="prompt-123",
    ) == {
        "code": "keeper_prompt_not_found",
        "message": "未找到 KP 提示 prompt-123",
        "scope": "keeper_prompt_prompt",
        "session_id": "session-321",
        "operator_id": "keeper-1",
        "prompt_id": "prompt-123",
    }
    assert build_session_action_error_detail(
        code="rollback_snapshot_not_found",
        message="未找到版本 9 的会话快照",
        scope="rollback_target",
        session_id="session-654",
        target_version=9,
    ) == {
        "code": "rollback_snapshot_not_found",
        "message": "未找到版本 9 的会话快照",
        "scope": "rollback_target",
        "session_id": "session-654",
        "target_version": 9,
    }
    assert build_session_action_error_detail(
        code="session_state_invalid",
        message="viewer_id is required for investigator views",
        scope="session_state_request",
        session_id="session-987",
        viewer_role="investigator",
    ) == {
        "code": "session_state_invalid",
        "message": "viewer_id is required for investigator views",
        "scope": "session_state_request",
        "session_id": "session-987",
        "viewer_role": "investigator",
    }
    assert build_rules_query_error_detail(
        code="rules_query_invalid",
        message="viewer_role invalid",
        scope="rules_query_request",
        query_text="理智检定",
        viewer_role="observer",
        viewer_id="investigator-1",
    ) == {
        "code": "rules_query_invalid",
        "message": "viewer_role invalid",
        "scope": "rules_query_request",
        "query_text": "理智检定",
        "viewer_role": "observer",
        "viewer_id": "investigator-1",
    }


def test_extract_error_detail_returns_dict_for_structured_errors_and_string_otherwise() -> None:
    structured = ValueError(
        build_structured_error_detail(
            code="sample_error",
            message="示例错误",
            scope="sample_scope",
        )
    )
    plain = ValueError("plain error")

    assert extract_error_detail(structured) == {
        "code": "sample_error",
        "message": "示例错误",
        "scope": "sample_scope",
    }
    assert extract_error_detail(plain) == "plain error"


def test_shape_validation_error_items_preserves_loc_message_type_and_optional_fields() -> None:
    assert shape_validation_error_items(
        [
            {
                "loc": ("participants", 0, "actor_id"),
                "msg": "Field required",
                "type": "missing",
                "input": {"name": "林舟"},
            },
            {
                "loc": ("query", "language_preference"),
                "msg": "Input should be 'zh-CN' or 'en-US'",
                "type": "enum",
                "input": "bad-lang",
                "ctx": {"expected": "'zh-CN' or 'en-US'"},
            },
        ]
    ) == [
        {
            "loc": ["participants", 0, "actor_id"],
            "message": "Field required",
            "type": "missing",
            "input": {"name": "林舟"},
        },
        {
            "loc": ["query", "language_preference"],
            "message": "Input should be 'zh-CN' or 'en-US'",
            "type": "enum",
            "input": "bad-lang",
            "ctx": {"expected": "'zh-CN' or 'en-US'"},
        },
    ]
    assert shape_validation_error_items(
        [
            {
                "loc": (),
                "msg": "Value error, participant actor_ids must be unique",
                "type": "value_error",
                "ctx": {"error": ValueError("participant actor_ids must be unique")},
            }
        ]
    ) == [
        {
            "loc": [],
            "message": "Value error, participant actor_ids must be unique",
            "type": "value_error",
            "ctx": {"error": "participant actor_ids must be unique"},
        }
    ]


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


def test_request_validation_query_errors_use_structured_422_detail_for_import_endpoint(
    client: TestClient,
) -> None:
    response = client.post(
        "/sessions/import",
        params={"language_preference": "bad-lang"},
        json={},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "request_validation_failed"
    assert detail["scope"] == "request_validation"
    assert detail["errors"] == [
        {
            "loc": ["query", "language_preference"],
            "message": "Input should be 'zh-CN' or 'en-US'",
            "type": "enum",
            "input": "bad-lang",
            "ctx": {"expected": "'zh-CN' or 'en-US'"},
        }
    ]


def test_request_validation_body_errors_use_structured_422_detail_for_rules_endpoint(
    client: TestClient,
) -> None:
    response = client.post(
        "/rules/query",
        json={"minimum_priority": -1},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "request_validation_failed"
    assert detail["scope"] == "request_validation"
    assert detail["errors"] == [
        {
            "loc": ["body", "query_text"],
            "message": "Field required",
            "type": "missing",
            "input": {"minimum_priority": -1},
        },
        {
            "loc": ["body", "minimum_priority"],
            "message": "Input should be greater than or equal to 0",
            "type": "greater_than_equal",
            "input": -1,
            "ctx": {"ge": 0},
        },
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
