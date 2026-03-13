from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from coc_runner.domain.scenario_examples import whispering_guesthouse_payload
from tests.helpers import make_participant
from tests.test_session_import import (
    KEEPER_ID,
    _create_checkpoint,
    _get_snapshot,
    _import_character_sheet_source,
    _import_snapshot,
    _make_cross_environment_client,
    _start_snapshot_session,
)


def _start_keeper_dashboard_session(client: TestClient) -> str:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "scenario": whispering_guesthouse_payload(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def _advance_keeper_dashboard_session(client: TestClient, session_id: str) -> None:
    lobby_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我趁老板转身时抽出柜台后的旧图纸并溜进账房。",
            "structured_action": {"type": "sneak_into_office"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene.guesthouse_office"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue.old_floorplan",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "sneak_into_office",
                    }
                ],
            },
        },
    )
    assert lobby_action.status_code == 202

    draft_response = client.post(
        f"/sessions/{session_id}/kp-draft",
        json={
            "draft_text": "KP 草稿：若调查员继续追问秦老板，应准备对话压力。",
            "structured_action": {"type": "kp_note"},
        },
    )
    assert draft_response.status_code == 202


def test_keeper_dashboard_displays_summary_attention_activity_and_checkpoint_links(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    _create_checkpoint(
        client,
        session_id,
        label="账房保留点",
        note="账房推进后的主持人分支点。",
        operator_id=KEEPER_ID,
    )

    response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "主持人工作台" in html
    assert session_id in html
    assert "雾港旅店的低语" in html
    assert "旅店账房" in html
    assert "beat.office_records" in html
    assert "核对账房记录" in html
    assert "找到能指向地窖的记录" in html
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in html
    assert "KP 草稿：若调查员继续追问秦老板，应准备对话压力。" in html
    assert "账房保留点" in html
    assert f'/playtest/sessions/{session_id}"' in html
    assert f'/sessions/{session_id}/snapshot"' in html
    assert f'/sessions/{session_id}/export"' in html
    assert html.index("我趁老板转身时抽出柜台后的旧图纸并溜进账房。") < html.index(
        "会话已创建：雾港旅店的低语"
    )


def test_keeper_dashboard_shows_natural_empty_states_without_optional_data(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)

    response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "主持人工作台" in html
    assert "当前没有待处理的 KP 提示。" in html
    assert "当前没有待审草稿。" in html
    assert "当前没有未完成目标。" in html
    assert "还没有检查点。先去创建一个用于回放或分支。" in html
    assert "当前环境缺少外部知识源" not in html


def test_keeper_dashboard_surfaces_missing_external_source_warnings() -> None:
    source_client, source_run_dir = _make_cross_environment_client("keeper_dashboard_source")
    target_client, target_run_dir = _make_cross_environment_client("keeper_dashboard_target")
    source_id = "character-sheet-template-keeper-dashboard"
    try:
        with source_client, target_client:
            _import_character_sheet_source(source_client, source_id=source_id)
            start_response = source_client.post(
                "/sessions/start",
                json={
                    "keeper_name": "KP",
                    "keeper_id": KEEPER_ID,
                    "scenario": whispering_guesthouse_payload(),
                    "participants": [
                        make_participant(
                            "investigator-1",
                            "占位调查员",
                            imported_character_source_id=source_id,
                        )
                    ],
                },
            )
            assert start_response.status_code == 201
            snapshot = _get_snapshot(source_client, start_response.json()["session_id"])
            imported = _import_snapshot(target_client, snapshot)

            response = target_client.get(f"/playtest/sessions/{imported['new_session_id']}/keeper")

            assert response.status_code == 200
            html = response.text
            assert "当前环境缺少外部知识源" in html
            assert "后续角色再同步可能降级" in html
            assert source_id in html
    finally:
        shutil.rmtree(source_run_dir, ignore_errors=True)
        shutil.rmtree(target_run_dir, ignore_errors=True)


def test_keeper_dashboard_missing_session_gracefully_renders_structured_error(
    client: TestClient,
) -> None:
    response = client.get("/playtest/sessions/session-missing/keeper")

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "未找到会话 session-missing" in html
    assert "session_state_session_not_found" in html
