from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import make_participant, make_scenario


def test_investigator_view_does_not_leak_other_private_state(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "language_preference": "zh-CN",
            "scenario": make_scenario(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    session_id = start_response.json()["session_id"]

    private_action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我偷偷把信纸藏进外套口袋。",
            "structured_action": {"type": "hide_item", "target": "letter"},
            "visibility_scope": "investigator_private",
        },
    )
    assert private_action_response.status_code == 202

    actor_one_view = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    )
    actor_two_view = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    )
    keeper_view = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )

    actor_one_payload = actor_one_view.json()
    actor_two_payload = actor_two_view.json()
    keeper_payload = keeper_view.json()

    assert any(
        event["text"] == "我偷偷把信纸藏进外套口袋。"
        for event in actor_one_payload["visible_events"]
    )
    assert all(
        event["text"] != "我偷偷把信纸藏进外套口袋。"
        for event in actor_two_payload["visible_events"]
    )
    assert actor_one_payload["own_private_state"]["private_notes"] == ["林舟 的私人笔记"]
    assert actor_two_payload["own_private_state"]["private_notes"] == ["周岚 的私人笔记"]
    assert actor_two_payload["visible_private_state_by_actor"] == {}
    assert actor_two_payload["behavior_memory_by_actor"] == {}
    assert set(keeper_payload["visible_private_state_by_actor"].keys()) == {
        "investigator-1",
        "investigator-2",
    }
