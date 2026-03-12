from __future__ import annotations

from fastapi.testclient import TestClient

from coc_runner.domain.errors import ConflictError
from tests.helpers import make_participant, make_scenario


def _start_session(client: TestClient) -> str:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "language_preference": "zh-CN",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    return start_response.json()["session_id"]


def _get_snapshot(client: TestClient, session_id: str) -> dict:
    snapshot_response = client.get(f"/sessions/{session_id}/snapshot")
    assert snapshot_response.status_code == 200
    return snapshot_response.json()


def test_session_rollback_restores_prior_snapshot(client: TestClient) -> None:
    session_id = _start_session(client)

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我检查地板上的泥脚印。",
            "structured_action": {"type": "investigate", "target": "muddy_footprints"},
        },
    )
    assert action_response.status_code == 202
    assert action_response.json()["state_version"] == 2

    rollback_response = client.post(
        f"/sessions/{session_id}/rollback",
        json={"target_version": 1},
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["state_version"] == 3

    state_response = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    visible_events = state_response.json()["visible_events"]
    texts = [event["text"] for event in visible_events]

    assert "我检查地板上的泥脚印。" not in texts
    assert any("回滚到版本 1" in text for text in texts)


def test_rollback_missing_session_returns_structured_404_with_language_override(
    client: TestClient,
) -> None:
    zh_response = client.post(
        "/sessions/missing-rollback/rollback",
        json={"target_version": 1},
    )
    assert zh_response.status_code == 404
    assert zh_response.json()["detail"] == {
        "code": "rollback_session_not_found",
        "message": "未找到会话 missing-rollback",
        "session_id": "missing-rollback",
        "target_version": 1,
        "scope": "rollback_session",
    }

    en_response = client.post(
        "/sessions/missing-rollback/rollback",
        json={"target_version": 1, "language_preference": "en-US"},
    )
    assert en_response.status_code == 404
    assert en_response.json()["detail"] == {
        "code": "rollback_session_not_found",
        "message": "Session missing-rollback was not found",
        "session_id": "missing-rollback",
        "target_version": 1,
        "scope": "rollback_session",
    }


def test_rollback_missing_snapshot_returns_structured_400_without_mutating_session(
    client: TestClient,
) -> None:
    session_id = _start_session(client)
    snapshot_before_rollback = _get_snapshot(client, session_id)

    rollback_response = client.post(
        f"/sessions/{session_id}/rollback",
        json={"target_version": 99},
    )

    assert rollback_response.status_code == 400
    assert rollback_response.json()["detail"] == {
        "code": "rollback_snapshot_not_found",
        "message": "未找到版本 99 的会话快照",
        "session_id": session_id,
        "target_version": 99,
        "scope": "rollback_target",
    }
    snapshot_after_rollback = _get_snapshot(client, session_id)
    assert snapshot_after_rollback == snapshot_before_rollback


def test_rollback_state_conflict_returns_structured_409_without_mutating_session(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_session(client)
    snapshot_before_rollback = _get_snapshot(client, session_id)
    repository = client.app.state.session_service.repository

    def _conflicting_rollback(
        session_id: str,
        *,
        target_version: int,
        event_text: str,
    ) -> None:
        raise ConflictError("会话状态版本冲突，请重新加载后再试")

    monkeypatch.setattr(repository, "rollback", _conflicting_rollback, raising=False)

    rollback_response = client.post(
        f"/sessions/{session_id}/rollback",
        json={"target_version": 1},
    )

    assert rollback_response.status_code == 409
    assert rollback_response.json()["detail"] == {
        "code": "rollback_state_conflict",
        "message": "会话状态版本冲突，请重新加载后再试",
        "session_id": session_id,
        "target_version": 1,
        "scope": "rollback_state",
    }
    snapshot_after_rollback = _get_snapshot(client, session_id)
    assert snapshot_after_rollback == snapshot_before_rollback
