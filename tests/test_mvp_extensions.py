from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    ActorType,
    DraftAction,
    EventType,
    ReviewDecision,
    ReviewDecisionType,
    ReviewedAction,
    ReviewStatus,
    SessionEvent,
    VisibilityScope,
)
from tests.helpers import make_participant, make_scenario


def _start_session(
    client: TestClient,
    *,
    allow_test_mode_self_review: bool = False,
    participants: list[dict] | None = None,
) -> str:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "allow_test_mode_self_review": allow_test_mode_self_review,
            "scenario": make_scenario(),
            "participants": participants
            or [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def _create_ai_draft(
    client: TestClient,
    session_id: str,
    *,
    action_text: str,
    structured_action: dict,
) -> str:
    response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": action_text,
            "structured_action": structured_action,
        },
    )
    assert response.status_code == 202
    return response.json()["draft_action"]["draft_id"]


def test_only_keeper_can_review_by_default(client: TestClient) -> None:
    session_id = _start_session(client)
    draft_id = _create_ai_draft(
        client,
        session_id,
        action_text="我建议先检查窗台。",
        structured_action={"type": "suggest_action", "target": "windowsill"},
    )

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={"reviewer_id": "ai-1", "decision": "approve"},
    )
    assert review_response.status_code == 403
    assert "只有本局 KP 可以审核该草稿" in review_response.json()["detail"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert keeper_state["visible_reviewed_actions"] == []
    assert keeper_state["visible_draft_actions"][0]["review_status"] == "pending"


def test_test_mode_self_review_can_be_enabled_explicitly(client: TestClient) -> None:
    session_id = _start_session(client, allow_test_mode_self_review=True)
    draft_id = _create_ai_draft(
        client,
        session_id,
        action_text="我建议先搜查楼梯。",
        structured_action={"type": "suggest_action", "target": "stairs"},
    )

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={"reviewer_id": "ai-1", "decision": "approve"},
    )
    assert review_response.status_code == 200
    assert review_response.json()["reviewed_action"]["actor_id"] == "ai-1"


def test_risk_level_escalates_from_server_rules_even_with_low_hint(client: TestClient) -> None:
    session_id = _start_session(client)

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议让这位 NPC 当场死亡。",
            "structured_action": {
                "type": "death",
                "risk_level": "low",
                "affects_state": ["npc_status"],
            },
        },
    )
    payload = draft_response.json()["draft_action"]

    assert payload["risk_level"] == "critical"
    assert payload["requires_explicit_approval"] is True
    assert "death" in payload["affects_state"]
    assert "npc_status" in payload["affects_state"]


def test_core_clue_flag_hint_requires_explicit_approval(client: TestClient) -> None:
    session_id = _start_session(client)

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议仔细检查那封求助信。",
            "structured_action": {
                "type": "investigate",
                "target": "letter",
                "core_clue_flag": True,
            },
        },
    )
    payload = draft_response.json()["draft_action"]

    assert payload["core_clue_flag"] is True
    assert payload["requires_explicit_approval"] is True
    assert payload["risk_level"] == "low"


def test_learn_from_final_false_skips_behavior_memory_update(client: TestClient) -> None:
    session_id = _start_session(client)
    draft_id = _create_ai_draft(
        client,
        session_id,
        action_text="我建议先调查储物室。",
        structured_action={"type": "suggest_action", "target": "storage_room"},
    )

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={
            "reviewer_id": "keeper-1",
            "decision": "approve",
            "learn_from_final": False,
        },
    )
    assert review_response.status_code == 200
    assert review_response.json()["reviewed_action"]["learn_from_final"] is False

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert keeper_state["behavior_memory_by_actor"] == {}

    next_draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我再给出一个新建议。",
            "structured_action": {"type": "suggest_action", "target": "kitchen"},
        },
    )
    assert next_draft_response.json()["draft_action"]["behavior_context"] == []


def test_optimistic_locking_raises_conflict_error_on_stale_save(client: TestClient) -> None:
    session_id = _start_session(
        client,
        participants=[make_participant("investigator-1", "林舟")],
    )
    repository = client.app.state.session_service.repository
    session_a = repository.get(session_id)
    session_b = repository.get(session_id)
    assert session_a is not None
    assert session_b is not None

    now = datetime.now(timezone.utc)
    session_a.timeline.append(
        SessionEvent(
            event_type=EventType.PLAYER_ACTION,
            actor_id="investigator-1",
            actor_type=ActorType.INVESTIGATOR,
            visibility_scope=VisibilityScope.PUBLIC,
            text="第一次更新",
            created_at=now,
        )
    )
    session_a.state_version += 1
    session_a.updated_at = now
    repository.save(session_a, reason="optimistic_lock_success", expected_version=1)

    session_b.timeline.append(
        SessionEvent(
            event_type=EventType.PLAYER_ACTION,
            actor_id="investigator-1",
            actor_type=ActorType.INVESTIGATOR,
            visibility_scope=VisibilityScope.PUBLIC,
            text="第二次更新",
            created_at=now,
        )
    )
    session_b.state_version += 1
    session_b.updated_at = now
    with pytest.raises(ConflictError):
        repository.save(session_b, reason="optimistic_lock_conflict", expected_version=1)


def test_repository_conflict_is_returned_as_http_409(client: TestClient) -> None:
    session_id = _start_session(
        client,
        participants=[make_participant("investigator-1", "林舟")],
    )
    service = client.app.state.session_service
    original_repository = service.repository

    class ConflictRepository:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def create(self, session, *, reason: str) -> None:
            self._wrapped.create(session, reason=reason)

        def get(self, session_id: str):
            return self._wrapped.get(session_id)

        def save(self, session, *, reason: str, expected_version: int) -> None:
            raise ConflictError("会话状态版本冲突，请重新加载后再试")

        def rollback(self, session_id: str, *, target_version: int, event_text: str):
            return self._wrapped.rollback(
                session_id,
                target_version=target_version,
                event_text=event_text,
            )

    service.repository = ConflictRepository(original_repository)
    try:
        response = client.post(
            f"/sessions/{session_id}/player-action",
            json={
                "actor_id": "investigator-1",
                "action_text": "我检查书桌。",
                "structured_action": {"type": "investigate", "target": "desk"},
            },
        )
    finally:
        service.repository = original_repository

    assert response.status_code == 409
    assert response.json()["detail"] == "会话状态版本冲突，请重新加载后再试"


def test_shared_subset_visibility_only_reaches_selected_viewers(client: TestClient) -> None:
    session_id = _start_session(
        client,
        participants=[
            make_participant("investigator-1", "林舟"),
            make_participant("investigator-2", "周岚"),
        ],
    )
    response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我只把这条消息告诉周岚。",
            "structured_action": {"type": "share_note"},
            "visibility_scope": "shared_subset",
            "visible_to": ["investigator-2"],
        },
    )
    assert response.status_code == 202

    actor_one = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    actor_two = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()
    keeper = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()

    assert all("我只把这条消息告诉周岚。" != event["text"] for event in actor_one["visible_events"])
    assert any("我只把这条消息告诉周岚。" == event["text"] for event in actor_two["visible_events"])
    assert any("我只把这条消息告诉周岚。" == event["text"] for event in keeper["visible_events"])


def test_system_internal_items_are_hidden_from_keeper_and_investigators(client: TestClient) -> None:
    session_id = _start_session(
        client,
        participants=[
            make_participant("investigator-1", "林舟"),
            make_participant("investigator-2", "周岚"),
        ],
    )
    repository = client.app.state.session_service.repository
    session = repository.get(session_id)
    assert session is not None

    now = datetime.now(timezone.utc)
    session.timeline.append(
        SessionEvent(
            event_type=EventType.PLAYER_ACTION,
            actor_type=ActorType.SYSTEM,
            visibility_scope=VisibilityScope.SYSTEM_INTERNAL,
            text="system internal event",
            structured_payload={"kind": "internal"},
            created_at=now,
        )
    )
    session.draft_actions.append(
        DraftAction(
            actor_id="system",
            actor_type=ActorType.SYSTEM,
            visibility_scope=VisibilityScope.SYSTEM_INTERNAL,
            draft_text="system internal draft",
            created_at_version=session.state_version + 1,
            created_at=now,
        )
    )
    session.reviewed_actions.append(
        ReviewedAction(
            draft_id="draft-internal",
            actor_id="system",
            actor_type=ActorType.SYSTEM,
            visibility_scope=VisibilityScope.SYSTEM_INTERNAL,
            review_status=ReviewStatus.APPROVED,
            final_text="system internal reviewed",
            decision=ReviewDecision(decision=ReviewDecisionType.APPROVE),
            created_at=now,
        )
    )
    session.state_version += 1
    session.updated_at = now
    repository.save(session, reason="system_internal_seed", expected_version=1)

    keeper = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    assert all(event["text"] != "system internal event" for event in keeper["visible_events"])
    assert all(draft["draft_text"] != "system internal draft" for draft in keeper["visible_draft_actions"])
    assert all(
        reviewed["final_text"] != "system internal reviewed"
        for reviewed in keeper["visible_reviewed_actions"]
    )
    assert all(event["text"] != "system internal event" for event in investigator["visible_events"])


def test_kp_draft_path_creates_reviewable_keeper_draft(client: TestClient) -> None:
    session_id = _start_session(
        client,
        participants=[make_participant("investigator-1", "林舟")],
    )
    draft_response = client.post(
        f"/sessions/{session_id}/kp-draft",
        json={
            "draft_text": "KP 暂定切换到地下室场景。",
            "structured_action": {"type": "scene_transition"},
        },
    )
    payload = draft_response.json()

    assert draft_response.status_code == 202
    assert payload["draft_action"]["actor_type"] == "keeper"
    assert payload["draft_action"]["actor_id"] == "keeper-1"
    assert payload["draft_action"]["risk_level"] == "high"
    assert payload["draft_action"]["requires_explicit_approval"] is True

    keeper_before_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert all(
        event["text"] != "KP 暂定切换到地下室场景。"
        for event in keeper_before_review["visible_events"]
    )

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{payload['draft_action']['draft_id']}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 200

    keeper_after_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert any(
        event["text"] == "KP 暂定切换到地下室场景。"
        for event in keeper_after_review["visible_events"]
    )


def test_stale_draft_review_returns_conflict_after_state_advances(client: TestClient) -> None:
    session_id = _start_session(client)
    draft_id = _create_ai_draft(
        client,
        session_id,
        action_text="我建议先搜索温室。",
        structured_action={"type": "suggest_action", "target": "greenhouse"},
    )

    for index in range(3):
        response = client.post(
            f"/sessions/{session_id}/player-action",
            json={
                "actor_id": "investigator-1",
                "action_text": f"我进行第 {index + 1} 次额外调查。",
                "structured_action": {"type": "investigate", "target": f"area-{index}"},
            },
        )
        assert response.status_code == 202

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 409
    assert "已过期" in review_response.json()["detail"]


def test_regenerated_draft_supersedes_original_canonical_outcome(client: TestClient) -> None:
    session_id = _start_session(client)
    original_draft_id = _create_ai_draft(
        client,
        session_id,
        action_text="我建议检查前门。",
        structured_action={"type": "suggest_action", "target": "front_door"},
    )

    regenerate_response = client.post(
        f"/sessions/{session_id}/draft-actions/{original_draft_id}/review",
        json={
            "reviewer_id": "keeper-1",
            "decision": "regenerate",
            "regenerated_draft_text": "我建议检查后门。",
            "regenerated_structured_action": {"type": "suggest_action", "target": "back_door"},
        },
    )
    assert regenerate_response.status_code == 200
    replacement_draft_id = regenerate_response.json()["regenerated_draft"]["draft_id"]

    original_review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{original_draft_id}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert original_review_response.status_code == 400

    replacement_review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{replacement_draft_id}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert replacement_review_response.status_code == 200

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert any(event["text"] == "我建议检查后门。" for event in keeper_state["visible_events"])
    assert all(event["text"] != "我建议检查前门。" for event in keeper_state["visible_events"])


def test_session_not_found_uses_request_language_or_default(client: TestClient) -> None:
    zh_response = client.post(
        "/sessions/missing-session/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我查看现场。",
            "structured_action": {"type": "investigate"},
        },
    )
    assert zh_response.status_code == 404
    assert zh_response.json()["detail"] == "未找到会话 missing-session"

    en_response = client.post(
        "/sessions/missing-session/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "I inspect the scene.",
            "structured_action": {"type": "investigate"},
            "language_preference": "en-US",
        },
    )
    assert en_response.status_code == 404
    assert en_response.json()["detail"] == "Session missing-session was not found"

    state_response = client.get(
        "/sessions/missing-session/state",
        params={"viewer_role": "keeper", "language_preference": "en-US"},
    )
    assert state_response.status_code == 404
    assert state_response.json()["detail"] == "Session missing-session was not found"


def test_rollback_event_text_uses_request_language_override(client: TestClient) -> None:
    session_id = _start_session(
        client,
        participants=[make_participant("investigator-1", "林舟")],
    )
    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我检查门锁。",
            "structured_action": {"type": "investigate", "target": "door_lock"},
        },
    )
    assert action_response.status_code == 202

    rollback_response = client.post(
        f"/sessions/{session_id}/rollback",
        json={"target_version": 1, "language_preference": "en-US"},
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["message"] == "Session rolled back to version 1"

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert any(
        event["text"] == "Session rolled back from version 2 to version 1"
        for event in keeper_state["visible_events"]
    )


def test_audit_log_tracks_draft_review_and_rollback(client: TestClient) -> None:
    session_id = _start_session(client)
    draft_id = _create_ai_draft(
        client,
        session_id,
        action_text="我建议先调查地下室。",
        structured_action={"type": "suggest_action", "target": "basement"},
    )
    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 200

    rollback_response = client.post(
        f"/sessions/{session_id}/rollback",
        json={"target_version": 1},
    )
    assert rollback_response.status_code == 200

    repository = client.app.state.session_service.repository
    session = repository.get(session_id)
    assert session is not None
    actions = [entry.action.value for entry in session.audit_log]
    assert "draft_created" in actions
    assert "review_decision" in actions
    assert "rollback" in actions


def test_behavior_memory_is_capped_per_actor(client: TestClient) -> None:
    session_id = _start_session(client)

    for index in range(6):
        draft_id = _create_ai_draft(
            client,
            session_id,
            action_text=f"我建议执行方案 {index}。",
            structured_action={"type": "suggest_action", "target": f"plan-{index}"},
        )
        review_response = client.post(
            f"/sessions/{session_id}/draft-actions/{draft_id}/review",
            json={"reviewer_id": "keeper-1", "decision": "approve"},
        )
        assert review_response.status_code == 200

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    memory = keeper_state["behavior_memory_by_actor"]["ai-1"]
    texts = [entry["final_text"] for entry in memory]

    assert len(memory) == 5
    assert "我建议执行方案 0。" not in texts
    assert texts[-1] == "我建议执行方案 5。"
