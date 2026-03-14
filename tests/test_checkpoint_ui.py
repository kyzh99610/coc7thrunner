from __future__ import annotations

import json
import re
import shutil

from fastapi.testclient import TestClient

from tests.helpers import make_participant
from tests.test_session_import import (
    KEEPER_ID,
    _create_checkpoint,
    _export_checkpoint,
    _get_snapshot,
    _import_character_sheet_source,
    _import_snapshot,
    _list_checkpoints,
    _make_cross_environment_client,
    _snapshot_scenario,
    _start_snapshot_session,
)


def test_session_checkpoint_page_displays_list_and_actions(client: TestClient) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="发现纸条后",
        operator_id=KEEPER_ID,
    )["checkpoint"]

    response = client.get(f"/playtest/sessions/{session_id}")

    assert response.status_code == 200
    html = response.text
    assert "检查点" in html
    assert "创建检查点" in html
    assert "导入检查点" in html
    assert 'name="checkpoint_payload"' in html
    assert "发现纸条后" in html
    assert "未写备注" in html
    assert str(checkpoint["source_session_version"]) in html
    assert checkpoint["created_by"] in html
    assert 'href="/playtest/sessions"' in html
    assert f'/playtest/sessions/{session_id}/home"' in html
    assert f'/playtest/sessions/{session_id}/checkpoints/{checkpoint["checkpoint_id"]}/export' in html
    assert f"/playtest/sessions/{session_id}/checkpoints/{checkpoint['checkpoint_id']}/restore" in html
    assert "恢复会创建一个新的 session，不会覆盖当前 session。确定继续吗？" in html
    assert "确认删除该检查点吗？" in html


def test_checkpoint_ui_export_page_displays_copyable_json_without_mutating_state(client: TestClient) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="导出用检查点",
        note="导出给另一台环境。",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoints_before_export = _list_checkpoints(client, session_id)
    snapshot_before_export = _get_snapshot(client, session_id)

    response = client.get(
        f"/playtest/sessions/{session_id}/checkpoints/{checkpoint['checkpoint_id']}/export"
    )

    assert response.status_code == 200
    html = response.text
    assert "导出检查点" in html
    assert "可复制的 checkpoint JSON" in html
    assert "&quot;format_version&quot;: 1" in html
    assert f"&quot;checkpoint_id&quot;: &quot;{checkpoint['checkpoint_id']}&quot;" in html
    assert f"&quot;source_session_id&quot;: &quot;{session_id}&quot;" in html
    assert "&quot;snapshot_payload&quot;: {" in html
    assert f'/playtest/sessions/{session_id}' in html
    assert _list_checkpoints(client, session_id) == checkpoints_before_export
    assert _get_snapshot(client, session_id) == snapshot_before_export


def test_checkpoint_ui_create_edit_delete_flow_updates_page(client: TestClient) -> None:
    session_id = _start_snapshot_session(client)

    create_response = client.post(
        f"/playtest/sessions/{session_id}/checkpoints/create",
        data={
            "label": "前厅保留点",
            "note": "第一次存档。",
            "operator_id": KEEPER_ID,
        },
    )

    assert create_response.status_code == 200
    assert "检查点已创建" in create_response.text
    assert "前厅保留点" in create_response.text
    checkpoint_id = _list_checkpoints(client, session_id)["checkpoints"][0]["checkpoint_id"]

    update_response = client.post(
        f"/playtest/sessions/{session_id}/checkpoints/{checkpoint_id}/update",
        data={
            "label": "档案室前",
            "note": "更新后的备注。",
            "operator_id": KEEPER_ID,
        },
    )

    assert update_response.status_code == 200
    assert "检查点已更新" in update_response.text
    assert "档案室前" in update_response.text
    assert "更新后的备注。" in update_response.text
    assert "前厅保留点" not in update_response.text

    delete_response = client.post(
        f"/playtest/sessions/{session_id}/checkpoints/{checkpoint_id}/delete",
        data={},
    )

    assert delete_response.status_code == 200
    assert "检查点已删除" in delete_response.text
    assert "还没有检查点" in delete_response.text


def test_checkpoint_ui_import_form_accepts_exported_json_and_updates_page(client: TestClient) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="导入源检查点",
        note="准备复制再导入。",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    export_payload = _export_checkpoint(client, session_id, checkpoint["checkpoint_id"])

    import_response = client.post(
        f"/playtest/sessions/{session_id}/checkpoints/import",
        data={"checkpoint_payload": json.dumps(export_payload, ensure_ascii=False)},
    )

    assert import_response.status_code == 200
    html = import_response.text
    assert "检查点已导入" in html
    assert f"original_checkpoint_id: <code>{checkpoint['checkpoint_id']}</code>" in html
    listed = _list_checkpoints(client, session_id)["checkpoints"]
    assert len(listed) == 2
    assert checkpoint["checkpoint_id"] in html
    assert listed[0]["checkpoint_id"] in html
    assert listed[1]["checkpoint_id"] in html
    assert listed[0]["checkpoint_id"] != listed[1]["checkpoint_id"]


def test_checkpoint_ui_import_invalid_json_shows_structured_error_without_half_write(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoints_before_failure = _list_checkpoints(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/checkpoints/import",
        data={"checkpoint_payload": '{"format_version": 1, bad json'},
    )

    assert response.status_code == 400
    html = response.text
    assert "检查点导入载荷校验失败" in html
    assert "session_checkpoint_import_invalid_payload" in html
    assert "&quot;format_version&quot;" in html
    assert "bad json" in html
    assert _list_checkpoints(client, session_id) == checkpoints_before_failure


def test_checkpoint_ui_restore_shows_new_session_id_link_and_warnings() -> None:
    source_client, source_run_dir = _make_cross_environment_client("checkpoint_ui_source")
    target_client, target_run_dir = _make_cross_environment_client("checkpoint_ui_target")
    source_id = "character-sheet-template-checkpoint-ui"
    try:
        with source_client, target_client:
            _import_character_sheet_source(source_client, source_id=source_id)
            start_response = source_client.post(
                "/sessions/start",
                json={
                    "keeper_name": "KP",
                    "scenario": _snapshot_scenario(),
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
            source_session_id = start_response.json()["session_id"]
            snapshot = _get_snapshot(source_client, source_session_id)

            imported = _import_snapshot(target_client, snapshot)
            target_session_id = imported["new_session_id"]
            checkpoint = _create_checkpoint(
                target_client,
                target_session_id,
                label="跨环境恢复点",
                operator_id=KEEPER_ID,
            )["checkpoint"]

            restore_response = target_client.post(
                f"/playtest/sessions/{target_session_id}/checkpoints/{checkpoint['checkpoint_id']}/restore",
                data={},
            )

            assert restore_response.status_code == 200
            html = restore_response.text
            match = re.search(r"new_session_id: <code>(session-[0-9a-f]+)</code>", html)
            assert match is not None
            restored_session_id = match.group(1)
            assert "已从检查点恢复新会话" in html
            assert restored_session_id != target_session_id
            assert "后续角色再同步可能降级" in html
            assert f'/playtest/sessions/{restored_session_id}' in html

            continued_action = target_client.post(
                f"/sessions/{restored_session_id}/player-action",
                json={
                    "actor_id": "investigator-1",
                    "action_text": "我继续检查恢复后的会话是否还能推进。",
                },
            )
            assert continued_action.status_code == 202
            assert continued_action.json()["session_id"] == restored_session_id
    finally:
        shutil.rmtree(source_run_dir, ignore_errors=True)
        shutil.rmtree(target_run_dir, ignore_errors=True)


def test_checkpoint_ui_shows_structured_errors_instead_of_failing_silently(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint_id = _create_checkpoint(
        client,
        session_id,
        label="保留点",
        operator_id=KEEPER_ID,
    )["checkpoint"]["checkpoint_id"]

    response = client.post(
        f"/playtest/sessions/{session_id}/checkpoints/{checkpoint_id}/update",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 400
    html = response.text
    assert "检查点更新请求至少要提供 label 或 note。" in html
    assert "session_checkpoint_update_invalid" in html
    assert "保留点" in html
