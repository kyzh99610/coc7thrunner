from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import make_participant, make_scenario


def test_default_language_is_zh_cn_when_not_overridden(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(include_language=False),
            "participants": [make_participant("investigator-1", "林舟", include_language=False)],
        },
    )
    payload = start_response.json()

    assert start_response.status_code == 201
    assert payload["language_preference"] == "zh-CN"
    assert payload["keeper_view"]["language_preference"] == "zh-CN"
    assert payload["keeper_view"]["scenario"]["language_preference"] == "zh-CN"
    assert payload["keeper_view"]["visible_events"][0]["language_preference"] == "zh-CN"

    action_response = client.post(
        f"/sessions/{payload['session_id']}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我查看门边的脚印。",
            "structured_action": {"type": "investigate", "target": "footprints"},
        },
    )
    assert action_response.json()["language_preference"] == "zh-CN"


def test_explicit_language_override_is_respected(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "language_preference": "en-US",
            "scenario": make_scenario(include_language=False),
            "participants": [make_participant("investigator-1", "Lin Zhou")],
        },
    )
    payload = start_response.json()

    assert payload["language_preference"] == "en-US"
    assert payload["message"] == "Session created"
    assert payload["keeper_view"]["scenario"]["language_preference"] == "en-US"
