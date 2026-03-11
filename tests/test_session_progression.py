from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import make_participant, make_scenario


def test_human_player_action_is_recorded_authoritatively(client: TestClient) -> None:
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
    session_id = start_response.json()["session_id"]

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我检查门锁。",
            "structured_action": {"type": "investigate", "target": "door_lock"},
        },
    )
    payload = action_response.json()

    assert action_response.status_code == 202
    assert payload["draft_action"] is None
    assert payload["authoritative_event"]["text"] == "我检查门锁。"
    assert payload["language_preference"] == "zh-CN"
    assert payload["state_version"] == 2


def test_ai_player_action_creates_reviewable_draft(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "language_preference": "zh-CN",
            "scenario": make_scenario(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    session_id = start_response.json()["session_id"]

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议先检查壁炉。",
            "structured_action": {"type": "suggest_action", "target": "fireplace"},
        },
    )
    payload = action_response.json()

    assert action_response.status_code == 202
    assert payload["authoritative_event"] is None
    assert payload["draft_action"]["review_status"] == "pending"
    assert payload["draft_action"]["behavior_context"] == []
    assert payload["state_version"] == 2

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert all(
        event["event_type"] != "reviewed_action" for event in keeper_state["visible_events"]
    )
    assert all(
        event["text"] != "我建议先检查壁炉。" for event in keeper_state["visible_events"]
    )
    assert len(keeper_state["visible_draft_actions"]) == 1
