from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import make_participant, make_scenario


def test_rejected_draft_does_not_affect_canonical_history(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    session_id = start_response.json()["session_id"]

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我想直接烧掉那封信。",
            "structured_action": {"type": "destroy_item", "target": "letter"},
        },
    )
    draft_id = draft_response.json()["draft_action"]["draft_id"]

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={
            "reviewer_id": "keeper-1",
            "decision": "reject",
            "editor_notes": "这个建议不合适。",
        },
    )
    assert review_response.status_code == 200
    assert review_response.json()["reviewed_action"] is None

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    texts = [event["text"] for event in keeper_state["visible_events"]]

    assert "我想直接烧掉那封信。" not in texts
    assert keeper_state["visible_reviewed_actions"] == []
    assert keeper_state["visible_draft_actions"][0]["review_status"] == "rejected"
    assert keeper_state["behavior_memory_by_actor"] == {}


def test_edited_review_replaces_draft_for_behavior_memory(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    session_id = start_response.json()["session_id"]

    first_draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议先检查壁炉。",
            "structured_action": {"type": "suggest_action", "target": "fireplace"},
        },
    )
    draft_id = first_draft_response.json()["draft_action"]["draft_id"]

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={
            "reviewer_id": "keeper-1",
            "decision": "edit",
            "final_text": "我建议先检查书桌抽屉。",
            "final_structured_action": {"type": "suggest_action", "target": "desk_drawer"},
            "editor_notes": "更符合当前线索。",
        },
    )
    assert review_response.status_code == 200
    assert review_response.json()["reviewed_action"]["final_text"] == "我建议先检查书桌抽屉。"

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    texts = [event["text"] for event in keeper_state["visible_events"]]

    assert "我建议先检查壁炉。" not in texts
    assert "我建议先检查书桌抽屉。" in texts
    assert keeper_state["behavior_memory_by_actor"]["ai-1"][0]["final_text"] == "我建议先检查书桌抽屉。"

    second_draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我再提出一个新建议。",
            "structured_action": {"type": "suggest_action", "target": "new_clue"},
        },
    )
    second_draft = second_draft_response.json()["draft_action"]

    assert second_draft["behavior_context"][0]["final_text"] == "我建议先检查书桌抽屉。"
    assert all(
        precedent["final_text"] != "我建议先检查壁炉。"
        for precedent in second_draft["behavior_context"]
    )

