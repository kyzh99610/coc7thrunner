from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import make_participant, make_scenario


def test_session_rollback_restores_prior_snapshot(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "language_preference": "zh-CN",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    session_id = start_response.json()["session_id"]

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
