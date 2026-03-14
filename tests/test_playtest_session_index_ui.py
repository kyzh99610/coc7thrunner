from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

from tests.helpers import make_participant
from tests.test_session_import import KEEPER_ID, _start_snapshot_session
from tests.test_session_import import _snapshot_scenario


def _set_session_status(client: TestClient, session_id: str, target_status: str) -> None:
    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": target_status},
    )
    assert response.status_code == 200


def _start_grouped_snapshot_session(
    client: TestClient,
    *,
    playtest_group: str | None,
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


def test_playtest_session_index_lists_multiple_sessions_with_statuses_and_entry_links(
    client: TestClient,
) -> None:
    planned_session_id = _start_snapshot_session(client)
    active_session_id = _start_snapshot_session(client)
    paused_session_id = _start_snapshot_session(client)
    completed_session_id = _start_snapshot_session(client)

    _set_session_status(client, active_session_id, "active")
    _set_session_status(client, paused_session_id, "active")
    _set_session_status(client, paused_session_id, "paused")
    _set_session_status(client, completed_session_id, "active")
    _set_session_status(client, completed_session_id, "completed")

    response = client.get("/playtest/sessions")

    assert response.status_code == 200
    html = response.text
    assert "Playtest Sessions" in html
    assert 'href="/playtest/sessions/create"' in html
    assert 'href="/playtest/knowledge"' in html
    assert "旅店前厅" in html
    assert "beat-find-note" in html
    assert "计划中" in html
    assert "进行中" in html
    assert "已暂停" in html
    assert "已完成" in html
    for session_id in (
        planned_session_id,
        active_session_id,
        paused_session_id,
        completed_session_id,
    ):
        assert session_id in html
        assert f'/playtest/sessions/{session_id}/home"' in html
        assert f'/playtest/sessions/{session_id}/keeper"' in html
        assert f'/playtest/sessions/{session_id}"' in html


def test_playtest_session_index_shows_natural_empty_state_without_sessions(
    client: TestClient,
) -> None:
    response = client.get("/playtest/sessions")

    assert response.status_code == 200
    html = response.text
    assert "Playtest Sessions" in html
    assert 'href="/playtest/sessions/create"' in html
    assert 'href="/playtest/knowledge"' in html
    assert "当前还没有 session。先创建一局，再从这里进入。" in html


def test_playtest_session_index_groups_sessions_by_playtest_group_and_keeps_ungrouped_bucket(
    client: TestClient,
) -> None:
    group_name = "旅店线压力测试"
    grouped_one = _start_grouped_snapshot_session(client, playtest_group=group_name)
    grouped_two = _start_grouped_snapshot_session(client, playtest_group=group_name)
    ungrouped = _start_grouped_snapshot_session(client, playtest_group=None)

    response = client.get("/playtest/sessions")

    assert response.status_code == 200
    html = response.text
    assert f"分组：{group_name}" in html
    assert f'href="/playtest/groups/{quote(group_name)}"' in html
    assert "分组：未分组" in html
    assert grouped_one in html
    assert grouped_two in html
    assert ungrouped in html


def test_playtest_group_page_lists_group_sessions_and_entry_links(
    client: TestClient,
) -> None:
    group_name = "旅店线压力测试"
    grouped_one = _start_grouped_snapshot_session(client, playtest_group=group_name)
    grouped_two = _start_grouped_snapshot_session(client, playtest_group=group_name)

    response = client.get(f"/playtest/groups/{quote(group_name)}")

    assert response.status_code == 200
    html = response.text
    assert f"分组：{group_name}" in html
    assert "本组 session 数" in html
    assert grouped_one in html
    assert grouped_two in html
    assert f'/playtest/sessions/{grouped_one}/home"' in html
    assert f'/playtest/sessions/{grouped_one}/keeper"' in html
    assert f'/playtest/sessions/{grouped_one}"' in html
    assert f'href="/playtest/sessions/create?playtest_group={quote(group_name)}"' in html


def test_playtest_group_page_shows_natural_empty_state_for_unknown_group(
    client: TestClient,
) -> None:
    group_name = "不存在的测试批次"

    response = client.get(f"/playtest/groups/{quote(group_name)}")

    assert response.status_code == 200
    html = response.text
    assert f"分组：{group_name}" in html
    assert "当前分组下还没有 session。" in html
    assert f'href="/playtest/sessions/create?playtest_group={quote(group_name)}"' in html
