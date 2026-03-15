from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_keeper_dashboard_ui import (
    _advance_keeper_dashboard_session,
    _start_keeper_dashboard_session,
)
from tests.test_session_import import KEEPER_ID


def test_playtest_recap_page_displays_summary_and_key_timeline_entries(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200

    prompt_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "acknowledged",
            "note": "先记下老板失态。",
        },
    )
    assert prompt_response.status_code == 200

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )
    assert complete_response.status_code == 200

    response = client.get(f"/playtest/sessions/{session_id}/recap")

    assert response.status_code == 200
    html = response.text
    assert "会话回顾" in html
    assert session_id in html
    assert "KP：KP" in html
    assert "当前状态" in html
    assert "已完成" in html
    assert "旅店账房" in html
    assert "beat.office_records" in html
    assert "时间线" in html
    assert "Session 状态" in html
    assert "进行中" in html
    assert html.count("已完成") >= 2
    assert "玩家行动" in html
    assert "我趁老板转身时抽出柜台后的旧图纸并溜进账房。" in html
    assert "KP 提示处理" in html
    assert "KP 提示已更新为 acknowledged" in html
    assert f'/playtest/sessions/{session_id}/home"' in html


def test_playtest_recap_page_missing_session_gracefully_renders_error(
    client: TestClient,
) -> None:
    response = client.get("/playtest/sessions/session-missing/recap")

    assert response.status_code == 404
    html = response.text
    assert "会话回顾" in html
    assert "操作失败" in html
    assert "session_snapshot_session_not_found" in html
