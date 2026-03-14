from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

from tests.helpers import make_participant
from tests.test_session_import import KEEPER_ID, _get_snapshot, _start_snapshot_session
from tests.test_session_import import _snapshot_scenario


def _start_grouped_snapshot_session(
    client: TestClient,
    *,
    playtest_group: str,
) -> str:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "playtest_group": playtest_group,
            "scenario": _snapshot_scenario(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def test_playtest_launcher_page_displays_summary_and_all_entry_links(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client, with_second_investigator=True)

    response = client.get(f"/playtest/sessions/{session_id}/home")

    assert response.status_code == 200
    html = response.text
    assert "Playtest 入口" in html
    assert session_id in html
    assert "KP：KP" in html
    assert "当前场景：旅店前厅" in html
    assert "当前 beat：beat-find-note" in html
    assert "当前状态" in html
    assert "计划中" in html
    assert "planned" in html
    assert 'href="/playtest/sessions"' in html
    assert f'/playtest/sessions/{session_id}/keeper"' in html
    assert f'/playtest/sessions/{session_id}"' in html
    assert f'/playtest/sessions/{session_id}/investigator/investigator-1"' in html
    assert f'/playtest/sessions/{session_id}/investigator/investigator-2"' in html
    assert "林舟" in html
    assert "周岚" in html
    assert "返回本组" not in html


def test_playtest_launcher_page_shows_back_link_to_group_when_session_has_group(
    client: TestClient,
) -> None:
    group_name = "旅店线压力测试"
    session_id = _start_grouped_snapshot_session(client, playtest_group=group_name)

    response = client.get(f"/playtest/sessions/{session_id}/home")

    assert response.status_code == 200
    html = response.text
    assert f"分组：{group_name}" in html
    assert "返回本组" in html
    assert f'href="/playtest/groups/{quote(group_name)}"' in html


def test_playtest_launcher_page_shows_completed_status_hint_after_keeper_completes_session(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )
    assert complete_response.status_code == 200

    response = client.get(f"/playtest/sessions/{session_id}/home")

    assert response.status_code == 200
    html = response.text
    assert "当前状态" in html
    assert "已完成" in html
    assert "completed" in html
    assert "该局已完成" in html
    assert "可进入主持人工作台查看最小收尾摘要" in html


def test_playtest_launcher_page_shows_natural_empty_state_when_no_investigators(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_snapshot_session(client)
    snapshot = _get_snapshot(client, session_id)
    snapshot["participants"] = []
    empty_session_id = "session-empty-launcher"
    snapshot["session_id"] = empty_session_id

    service = client.app.state.session_service
    original_snapshot_session = service.snapshot_session

    def fake_snapshot_session(requested_session_id: str, *, language_preference=None):
        if requested_session_id == empty_session_id:
            return snapshot
        return original_snapshot_session(
            requested_session_id,
            language_preference=language_preference,
        )

    monkeypatch.setattr(service, "snapshot_session", fake_snapshot_session)

    response = client.get(f"/playtest/sessions/{empty_session_id}/home")

    assert response.status_code == 200
    html = response.text
    assert "当前没有可进入的调查员页面。" in html
    assert f'/playtest/sessions/{empty_session_id}/keeper"' in html
    assert f'/playtest/sessions/{empty_session_id}"' in html


def test_playtest_launcher_page_missing_session_gracefully_renders_error(
    client: TestClient,
) -> None:
    response = client.get("/playtest/sessions/session-missing/home")

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "未找到会话 session-missing" in html
    assert "session_snapshot_session_not_found" in html
