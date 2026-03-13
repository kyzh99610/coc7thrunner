from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from coc_runner.config import Settings
from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import SessionCheckpoint
from coc_runner.infrastructure.models import SessionCheckpointRecord, SessionRecord
from coc_runner.main import create_app
from tests.helpers import make_participant, make_scenario


KEEPER_ID = "keeper-1"
UPLOADED_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "coc7th rules and templates"
UPLOADED_TEMPLATE_SAMPLE_DIR = UPLOADED_TEMPLATE_DIR / "sample templates"


def _snapshot_scenario() -> dict:
    return make_scenario(
        start_scene_id="scene.foyer",
        scenes=[
            {
                "scene_id": "scene.foyer",
                "title": "旅店前厅",
                "summary": "潮湿前厅里摆着旧登记簿和一盏忽明忽暗的煤气灯。",
                "revealed": True,
                "linked_clue_ids": ["clue-note"],
            },
            {
                "scene_id": "scene.archive",
                "title": "档案室",
                "summary": "木架间堆满发潮卷宗，角落里压着一本残缺日记。",
                "revealed": False,
                "linked_clue_ids": ["clue-private-journal"],
            },
        ],
        clues=[
            {
                "clue_id": "clue-note",
                "title": "潮湿纸条",
                "text": "纸条提到档案室里还留着只应给找到者阅读的旧日记。",
                "visibility_scope": "kp_only",
            },
            {
                "clue_id": "clue-private-journal",
                "title": "残缺日记",
                "text": "日记只对真正翻到它的人暴露房内真相。",
                "visibility_scope": "kp_only",
            },
        ],
        beats=[
            {
                "beat_id": "beat-find-note",
                "title": "发现潮湿纸条",
                "start_unlocked": True,
                "complete_conditions": {
                    "clue_discovered": {"clue_id": "clue-note"}
                },
                "consequences": [
                    {
                        "reveal_scenes": [{"scene_id": "scene.archive"}],
                        "queue_kp_prompts": [
                            {
                                "prompt_text": "KP：档案室的低语压力已具备条件，请保留一次理智审阅。",
                                "category": "sanity_review",
                                "scene_id": "scene.foyer",
                                "reason": "潮湿纸条已经把调查指向档案室。",
                            }
                        ],
                    }
                ],
                "next_beats": ["beat-archive-truth"],
            },
            {
                "beat_id": "beat-archive-truth",
                "title": "确认档案室真相",
                "complete_conditions": {
                    "all_of": [
                        {"current_scene_in": {"scene_ids": ["scene.archive"]}},
                        {"clue_discovered": {"clue_id": "clue-private-journal"}},
                    ]
                },
            },
        ],
    )


def _high_risk_grounding_review_scenario() -> dict:
    return make_scenario(
        start_scene_id="scene.library",
        scenes=[
            {
                "scene_id": "scene.library",
                "title": "旅店阅览室",
                "summary": "昏黄煤气灯下的阅览室里散着旧报纸与借阅卡。",
                "revealed": True,
            }
        ],
    )


def _start_snapshot_session(
    client: TestClient,
    *,
    with_second_investigator: bool = False,
) -> str:
    participants = [make_participant("investigator-1", "林舟")]
    if with_second_investigator:
        participants.append(make_participant("investigator-2", "周岚"))

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "scenario": _snapshot_scenario(),
            "participants": participants,
        },
    )
    assert start_response.status_code == 201
    return start_response.json()["session_id"]


def _get_keeper_state(client: TestClient, session_id: str) -> dict:
    response = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert response.status_code == 200
    return response.json()


def _get_investigator_state(client: TestClient, session_id: str, actor_id: str) -> dict:
    response = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "investigator", "viewer_id": actor_id},
    )
    assert response.status_code == 200
    return response.json()


def _get_snapshot(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/snapshot")
    assert response.status_code == 200
    return response.json()


def _count_session_records(client: TestClient) -> int:
    repository = client.app.state.session_service.repository
    with repository.session_factory() as db:
        return db.execute(select(func.count()).select_from(SessionRecord)).scalar_one()


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _count_checkpoint_records(client: TestClient) -> int:
    repository = client.app.state.session_service.repository
    with repository.session_factory() as db:
        return db.execute(select(func.count()).select_from(SessionCheckpointRecord)).scalar_one()


def _import_snapshot(client: TestClient, snapshot: dict) -> dict:
    response = client.post("/sessions/import", json=snapshot)
    assert response.status_code == 201
    return response.json()


def _create_checkpoint(
    client: TestClient,
    session_id: str,
    *,
    label: str,
    note: str | None = None,
    operator_id: str | None = None,
) -> dict:
    response = client.post(
        f"/sessions/{session_id}/checkpoints",
        json={
            "label": label,
            "note": note,
            "operator_id": operator_id,
        },
    )
    assert response.status_code == 201
    return response.json()


def _list_checkpoints(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/checkpoints")
    assert response.status_code == 200
    return response.json()


def _update_checkpoint(
    client: TestClient,
    session_id: str,
    checkpoint_id: str,
    *,
    label: str | None = None,
    note: str | None = None,
    operator_id: str | None = None,
) -> dict:
    payload: dict[str, str | None] = {}
    if label is not None:
        payload["label"] = label
    if note is not None:
        payload["note"] = note
    if operator_id is not None:
        payload["operator_id"] = operator_id
    response = client.patch(
        f"/sessions/{session_id}/checkpoints/{checkpoint_id}",
        json=payload,
    )
    assert response.status_code == 200
    return response.json()


def _restore_checkpoint(client: TestClient, session_id: str, checkpoint_id: str) -> dict:
    response = client.post(
        f"/sessions/{session_id}/checkpoints/{checkpoint_id}/restore",
        json={},
    )
    assert response.status_code == 201
    return response.json()


def _export_checkpoint(client: TestClient, session_id: str, checkpoint_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/checkpoints/{checkpoint_id}/export")
    assert response.status_code == 200
    return response.json()


def _import_checkpoint_payload(client: TestClient, payload: dict) -> dict:
    response = client.post("/sessions/checkpoints/import", json=payload)
    assert response.status_code == 201
    return response.json()


def _delete_checkpoint(client: TestClient, session_id: str, checkpoint_id: str) -> dict:
    response = client.delete(f"/sessions/{session_id}/checkpoints/{checkpoint_id}")
    assert response.status_code == 200
    return response.json()


def _seed_checkpoint_record(
    client: TestClient,
    *,
    session_id: str,
    source_session_version: int,
    label: str,
    snapshot_payload: dict,
    note: str | None = None,
    created_by: str | None = None,
) -> SessionCheckpoint:
    checkpoint = SessionCheckpoint(
        checkpoint_id=f"checkpoint-{uuid4().hex}",
        source_session_id=session_id,
        source_session_version=source_session_version,
        label=label,
        note=note,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
        snapshot_payload=snapshot_payload,
    )
    client.app.state.session_service.repository.create_checkpoint(checkpoint)
    return checkpoint


def _register_character_sheet_source(client: TestClient, *, source_id: str) -> None:
    response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": "character_sheet",
            "source_format": "xlsx",
            "source_title_zh": "布鲁斯角色卡",
            "document_identity": source_id,
            "source_path": str(UPLOADED_TEMPLATE_SAMPLE_DIR / "Bruce vain.xlsx"),
            "default_priority": 0,
            "is_authoritative": False,
        },
    )
    assert response.status_code == 201


def _import_character_sheet_source(client: TestClient, *, source_id: str) -> dict:
    _register_character_sheet_source(client, source_id=source_id)
    response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": source_id},
    )
    assert response.status_code == 200
    return response.json()


def _register_text_rule_source(
    client: TestClient,
    *,
    source_id: str,
    source_title_zh: str,
    content: str,
    default_priority: int = 45,
) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": "rulebook",
            "source_format": "markdown",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
            "default_priority": default_priority,
            "is_authoritative": True,
        },
    )
    assert register_response.status_code == 201
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert ingest_response.status_code == 200


def _make_cross_environment_client(suffix: str) -> tuple[TestClient, Path]:
    base_dir = Path("test-artifacts")
    base_dir.mkdir(exist_ok=True)
    run_dir = base_dir / f"coc_runner_cross_env_{suffix}_{uuid4().hex}"
    run_dir.mkdir()
    db_path = run_dir / "coc_runner_cross_env.db"
    return TestClient(create_app(Settings(db_url=f"sqlite:///{db_path.as_posix()}"))), run_dir


def _discover_note_and_queue_prompt(client: TestClient, session_id: str) -> None:
    response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我在前厅登记簿下找到一张潮湿纸条，并把内容告诉大家。",
            "structured_action": {"type": "inspect_front_desk"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-note",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "inspect_front_desk",
                    }
                ]
            },
        },
    )
    assert response.status_code == 202


def _move_to_archive(client: TestClient, session_id: str) -> None:
    response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我顺着纸条指向前往档案室继续调查。",
            "structured_action": {"type": "move_to_archive"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene.archive"}]
            },
        },
    )
    assert response.status_code == 202


def _reveal_private_journal(client: TestClient, session_id: str) -> None:
    response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "actor_type": "investigator",
            "action_text": "KP 确认林舟在档案室角落找到一本只供自己阅读的残缺日记。",
            "structured_action": {"type": "confirm_private_journal"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-private-journal",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "confirm_private_journal",
                    }
                ]
            },
        },
    )
    assert response.status_code == 202


def _beat_statuses(state_payload: dict) -> dict[str, str]:
    return {
        beat["beat_id"]: beat["status"]
        for beat in state_payload["scenario"]["beats"]
    }


def _clue_statuses(state_payload: dict) -> dict[str, str]:
    return {
        clue["clue_id"]: clue["status"]
        for clue in state_payload["scenario"]["clues"]
    }


def test_snapshot_returns_raw_session_state(client: TestClient) -> None:
    session_id = _start_snapshot_session(client)

    snapshot = _get_snapshot(client, session_id)

    assert snapshot["session_id"] == session_id
    assert snapshot["keeper_id"] == KEEPER_ID
    assert snapshot["status"] == "active"
    assert "audit_log" in snapshot
    assert "timeline" in snapshot
    assert "participants" in snapshot
    assert "secrets" in snapshot["participants"][0]


def test_import_from_snapshot_creates_new_session(client: TestClient) -> None:
    original_session_id = _start_snapshot_session(client, with_second_investigator=True)
    _discover_note_and_queue_prompt(client, original_session_id)

    original_keeper_state = _get_keeper_state(client, original_session_id)
    snapshot = _get_snapshot(client, original_session_id)
    import_response = _import_snapshot(client, snapshot)

    new_session_id = import_response["new_session_id"]
    assert import_response["original_session_id"] == original_session_id
    assert new_session_id != original_session_id
    assert import_response["state_version"] == 1
    assert import_response["warnings"] == []

    imported_keeper_state = _get_keeper_state(client, new_session_id)
    imported_snapshot = _get_snapshot(client, new_session_id)

    assert imported_keeper_state["scenario"]["title"] == original_keeper_state["scenario"]["title"]
    assert imported_keeper_state["progress_state"]["current_beat"] == (
        original_keeper_state["progress_state"]["current_beat"]
    )
    assert _beat_statuses(imported_keeper_state) == _beat_statuses(original_keeper_state)
    assert _clue_statuses(imported_keeper_state) == _clue_statuses(original_keeper_state)
    assert imported_snapshot["timeline"][-1]["event_type"] == "import"
    assert imported_snapshot["timeline"][-1]["structured_payload"] == {
        "original_session_id": original_session_id,
        "original_version": snapshot["state_version"],
    }
    assert imported_snapshot["audit_log"][-1]["action"] == "import"
    assert imported_snapshot["audit_log"][-1]["session_version"] == 1


def test_import_invalid_snapshot_returns_structured_400_without_creating_session(
    client: TestClient,
) -> None:
    original_session_id = _start_snapshot_session(client)
    snapshot = _get_snapshot(client, original_session_id)
    session_count_before_import = _count_session_records(client)
    invalid_snapshot = dict(snapshot)
    invalid_snapshot["participants"] = "oops"

    import_response = client.post("/sessions/import", json=invalid_snapshot)

    assert import_response.status_code == 400
    detail = import_response.json()["detail"]
    assert detail["code"] == "session_import_invalid_snapshot"
    assert detail["message"] == "导入快照校验失败"
    assert detail["scope"] == "session_import_payload"
    assert detail["original_session_id"] == original_session_id
    assert any(
        error["loc"] == ["participants"]
        and error["message"] == "Input should be a valid list"
        and error["type"] == "list_type"
        and error["input"] == "oops"
        for error in detail["errors"]
    )
    session_count_after_import = _count_session_records(client)
    assert session_count_after_import == session_count_before_import


def test_import_state_conflict_returns_structured_409_without_creating_session(
    client: TestClient,
    monkeypatch,
) -> None:
    original_session_id = _start_snapshot_session(client)
    snapshot = _get_snapshot(client, original_session_id)
    session_count_before_import = _count_session_records(client)
    repository = client.app.state.session_service.repository

    def _conflicting_create(session, *, reason: str) -> None:
        raise ConflictError("会话状态版本冲突，请重新加载后再试")

    monkeypatch.setattr(repository, "create", _conflicting_create, raising=False)

    import_response = client.post("/sessions/import", json=snapshot)

    assert import_response.status_code == 409
    assert import_response.json()["detail"] == {
        "code": "session_import_state_conflict",
        "message": "会话状态版本冲突，请重新加载后再试",
        "scope": "session_import_state",
        "original_session_id": original_session_id,
        "original_version": snapshot["state_version"],
    }
    session_count_after_import = _count_session_records(client)
    assert session_count_after_import == session_count_before_import


def test_imported_session_supports_continued_play(client: TestClient) -> None:
    original_session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, original_session_id)

    snapshot = _get_snapshot(client, original_session_id)
    imported = _import_snapshot(client, snapshot)
    new_session_id = imported["new_session_id"]

    _move_to_archive(client, new_session_id)
    _reveal_private_journal(client, new_session_id)

    keeper_state = _get_keeper_state(client, new_session_id)
    visible_event_texts = [event["text"] for event in keeper_state["visible_events"]]
    clue_statuses = _clue_statuses(keeper_state)

    assert "我顺着纸条指向前往档案室继续调查。" in visible_event_texts
    assert "KP 确认林舟在档案室角落找到一本只供自己阅读的残缺日记。" in visible_event_texts
    assert keeper_state["progress_state"]["current_beat"] != "beat-find-note"
    assert _beat_statuses(keeper_state)["beat-archive-truth"] == "completed"
    assert clue_statuses["clue-private-journal"] == "private_to_actor"


def test_imported_session_preserves_keeper_investigator_isolation(client: TestClient) -> None:
    original_session_id = _start_snapshot_session(client, with_second_investigator=True)
    _discover_note_and_queue_prompt(client, original_session_id)
    _move_to_archive(client, original_session_id)
    _reveal_private_journal(client, original_session_id)

    snapshot = _get_snapshot(client, original_session_id)
    imported = _import_snapshot(client, snapshot)
    new_session_id = imported["new_session_id"]

    investigator_one = _get_investigator_state(client, new_session_id, "investigator-1")
    investigator_two = _get_investigator_state(client, new_session_id, "investigator-2")
    keeper_state = _get_keeper_state(client, new_session_id)

    actor_one_clues = {clue["clue_id"]: clue for clue in investigator_one["scenario"]["clues"]}
    actor_two_clues = {clue["clue_id"]: clue for clue in investigator_two["scenario"]["clues"]}
    keeper_clues = {clue["clue_id"]: clue for clue in keeper_state["scenario"]["clues"]}

    assert actor_one_clues["clue-private-journal"]["status"] == "private_to_actor"
    assert "clue-private-journal" not in actor_two_clues
    assert keeper_clues["clue-private-journal"]["status"] == "private_to_actor"
    assert "clue-private-journal" in investigator_one["own_character_state"]["clue_ids"]
    assert "clue-private-journal" not in investigator_two["own_character_state"]["clue_ids"]


def test_imported_session_preserves_prompt_lifecycle(client: TestClient) -> None:
    original_session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, original_session_id)

    original_keeper_state = _get_keeper_state(client, original_session_id)
    original_prompt = original_keeper_state["keeper_workflow"]["active_prompts"][0]

    snapshot = _get_snapshot(client, original_session_id)
    imported = _import_snapshot(client, snapshot)
    new_session_id = imported["new_session_id"]

    imported_keeper_state = _get_keeper_state(client, new_session_id)
    imported_prompt = imported_keeper_state["keeper_workflow"]["active_prompts"][0]

    assert imported_prompt["prompt_id"] == original_prompt["prompt_id"]
    assert imported_prompt["status"] == "pending"

    update_response = client.post(
        f"/sessions/{new_session_id}/keeper-prompts/{imported_prompt['prompt_id']}/status",
        json={
            "operator_id": KEEPER_ID,
            "status": "acknowledged",
            "add_notes": ["导入后继续处理这条待审提示。"],
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["prompt"]["status"] == "acknowledged"

    updated_keeper_state = _get_keeper_state(client, new_session_id)
    updated_prompt = updated_keeper_state["keeper_workflow"]["active_prompts"][0]
    assert updated_prompt["status"] == "acknowledged"
    assert "导入后继续处理这条待审提示。" in updated_prompt["notes"]


def test_duplicate_import_creates_independent_sessions(client: TestClient) -> None:
    original_session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, original_session_id)

    snapshot = _get_snapshot(client, original_session_id)
    first_import = _import_snapshot(client, snapshot)
    second_import = _import_snapshot(client, snapshot)

    first_session_id = first_import["new_session_id"]
    second_session_id = second_import["new_session_id"]
    assert first_session_id != second_session_id

    mutation_response = client.post(
        f"/sessions/{first_session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我只在第一个导入副本里检查档案室门口的脚印。",
            "structured_action": {"type": "inspect_archive_door"},
        },
    )
    assert mutation_response.status_code == 202

    first_keeper_state = _get_keeper_state(client, first_session_id)
    second_keeper_state = _get_keeper_state(client, second_session_id)
    first_event_texts = [event["text"] for event in first_keeper_state["visible_events"]]
    second_event_texts = [event["text"] for event in second_keeper_state["visible_events"]]

    assert "我只在第一个导入副本里检查档案室门口的脚印。" in first_event_texts
    assert "我只在第一个导入副本里检查档案室门口的脚印。" not in second_event_texts


def test_create_checkpoint_persists_named_snapshot_without_mutating_session(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, session_id)
    snapshot_before_checkpoint = _get_snapshot(client, session_id)
    checkpoint_count_before_create = _count_checkpoint_records(client)

    checkpoint_response = _create_checkpoint(
        client,
        session_id,
        label="发现纸条后",
        note="保留前厅推进点，便于回放档案室分支。",
        operator_id=KEEPER_ID,
    )

    assert checkpoint_response["message"] == "检查点已创建"
    assert checkpoint_response["session_id"] == session_id
    checkpoint = checkpoint_response["checkpoint"]
    assert checkpoint["checkpoint_id"].startswith("checkpoint-")
    assert checkpoint["source_session_id"] == session_id
    assert checkpoint["source_session_version"] == snapshot_before_checkpoint["state_version"]
    assert checkpoint["label"] == "发现纸条后"
    assert checkpoint["note"] == "保留前厅推进点，便于回放档案室分支。"
    assert checkpoint["created_by"] == KEEPER_ID
    assert _count_checkpoint_records(client) == checkpoint_count_before_create + 1
    assert _get_snapshot(client, session_id) == snapshot_before_checkpoint


def test_list_checkpoints_returns_desc_metadata_without_snapshot_payload(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    first_checkpoint = _create_checkpoint(
        client,
        session_id,
        label="前厅起点",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    second_checkpoint = _create_checkpoint(
        client,
        session_id,
        label="前厅保留点",
        note="最近一次检查点。",
        operator_id=KEEPER_ID,
    )["checkpoint"]

    response = client.get(f"/sessions/{session_id}/checkpoints")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == session_id
    checkpoints = payload["checkpoints"]
    assert {
        checkpoint["checkpoint_id"] for checkpoint in checkpoints
    } == {
        first_checkpoint["checkpoint_id"],
        second_checkpoint["checkpoint_id"],
    }
    assert checkpoints == sorted(
        checkpoints,
        key=lambda checkpoint: (checkpoint["created_at"], checkpoint["checkpoint_id"]),
        reverse=True,
    )
    assert all("snapshot_payload" not in checkpoint for checkpoint in checkpoints)
    assert all("raw_snapshot" not in checkpoint for checkpoint in checkpoints)


def test_export_checkpoint_returns_stable_payload_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, session_id)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="发现纸条后",
        note="准备导出给另一台机器。",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    snapshot_before_export = _get_snapshot(client, session_id)
    checkpoint_count_before_export = _count_checkpoint_records(client)

    export_payload = _export_checkpoint(client, session_id, checkpoint["checkpoint_id"])

    assert export_payload["format_version"] == 1
    assert export_payload["exported_at"]
    exported_checkpoint = export_payload["checkpoint"]
    assert exported_checkpoint["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert exported_checkpoint["source_session_id"] == session_id
    assert exported_checkpoint["source_session_version"] == checkpoint["source_session_version"]
    assert exported_checkpoint["label"] == "发现纸条后"
    assert exported_checkpoint["note"] == "准备导出给另一台机器。"
    assert exported_checkpoint["created_by"] == KEEPER_ID
    assert exported_checkpoint["snapshot_payload"] == snapshot_before_export
    assert _count_checkpoint_records(client) == checkpoint_count_before_export
    assert _get_snapshot(client, session_id) == snapshot_before_export


def test_export_checkpoint_missing_session_returns_structured_404_without_mutating_state(
    client: TestClient,
) -> None:
    checkpoint_count_before_request = _count_checkpoint_records(client)

    response = client.get("/sessions/session-missing/checkpoints/checkpoint-missing/export")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "session_checkpoint_session_not_found",
        "message": "未找到会话 session-missing",
        "scope": "session_checkpoint_session",
        "session_id": "session-missing",
    }
    assert _count_checkpoint_records(client) == checkpoint_count_before_request


def test_export_checkpoint_missing_checkpoint_returns_structured_404_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint_count_before_request = _count_checkpoint_records(client)
    snapshot_before_request = _get_snapshot(client, session_id)

    response = client.get(f"/sessions/{session_id}/checkpoints/checkpoint-missing/export")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "session_checkpoint_not_found",
        "message": "未找到检查点 checkpoint-missing",
        "scope": "session_checkpoint_record",
        "session_id": session_id,
        "checkpoint_id": "checkpoint-missing",
        "source_session_id": session_id,
    }
    assert _count_checkpoint_records(client) == checkpoint_count_before_request
    assert _get_snapshot(client, session_id) == snapshot_before_request


def test_restore_checkpoint_creates_new_session_and_preserves_original_session(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, session_id)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="发现纸条后",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoint_snapshot = _get_snapshot(client, session_id)

    _move_to_archive(client, session_id)
    original_snapshot_before_restore = _get_snapshot(client, session_id)
    assert original_snapshot_before_restore["current_scene"]["scene_id"] == "scene.archive"
    original_event_texts_before_restore = [
        event["text"] for event in original_snapshot_before_restore["timeline"]
    ]
    assert "我顺着纸条指向前往档案室继续调查。" in original_event_texts_before_restore

    restore_response = client.post(
        f"/sessions/{session_id}/checkpoints/{checkpoint['checkpoint_id']}/restore",
        json={},
    )

    assert restore_response.status_code == 201
    restore_payload = restore_response.json()
    restored_session_id = restore_payload["new_session_id"]
    assert restored_session_id != session_id
    assert restore_payload["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert restore_payload["source_session_id"] == session_id
    assert restore_payload["warnings"] == []

    restored_snapshot = _get_snapshot(client, restored_session_id)
    restored_event_texts = [event["text"] for event in restored_snapshot["timeline"]]
    assert restored_snapshot["current_scene"]["scene_id"] == checkpoint_snapshot["current_scene"]["scene_id"]
    assert "我顺着纸条指向前往档案室继续调查。" not in restored_event_texts
    assert restored_snapshot["timeline"][-1]["event_type"] == "import"

    player_action_response = client.post(
        f"/sessions/{restored_session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我在恢复副本里继续检查前厅煤气灯。",
            "structured_action": {"type": "inspect_gas_lamp"},
        },
    )
    assert player_action_response.status_code == 202

    assert _get_snapshot(client, session_id) == original_snapshot_before_restore


def test_restore_checkpoint_inherits_import_warnings_for_missing_external_sources(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-checkpoint-cross-env"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
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
    original_session_id = start_response.json()["session_id"]
    _discover_note_and_queue_prompt(client, original_session_id)
    snapshot = _get_snapshot(client, original_session_id)

    restore_env_client, run_dir = _make_cross_environment_client("checkpoint_missing_sources")
    try:
        with restore_env_client:
            checkpoint = _seed_checkpoint_record(
                restore_env_client,
                session_id=original_session_id,
                source_session_version=snapshot["state_version"],
                label="跨环境恢复点",
                note="故意不带外部 source，验证 warning 继承。",
                created_by=KEEPER_ID,
                snapshot_payload=snapshot,
            )

            restore_response = restore_env_client.post(
                f"/sessions/{original_session_id}/checkpoints/{checkpoint.checkpoint_id}/restore",
                json={},
            )

            assert restore_response.status_code == 201
            restore_payload = restore_response.json()
            warnings = restore_payload["warnings"]
            assert warnings
            assert all(warning["code"] == "missing_external_source" for warning in warnings)
            assert {
                "participant.imported_character_source_id",
                "character_state.import_source_id",
                "character_state.secret_state_refs",
            }.issubset({warning["scope"] for warning in warnings})
            assert any(warning.get("source_id") == source_id for warning in warnings)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_import_checkpoint_payload_creates_manageable_restorable_checkpoint_with_new_id(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, session_id)
    original_checkpoint = _create_checkpoint(
        client,
        session_id,
        label="导出前检查点",
        note="用于验证 checkpoint import。",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    export_payload = _export_checkpoint(client, session_id, original_checkpoint["checkpoint_id"])

    import_env_client, run_dir = _make_cross_environment_client("checkpoint_import_roundtrip")
    try:
        with import_env_client:
            checkpoint_count_before_import = _count_checkpoint_records(import_env_client)
            import_payload = _import_checkpoint_payload(import_env_client, export_payload)

            assert import_payload["message"] == "检查点已导入"
            imported_checkpoint = import_payload["checkpoint"]
            assert import_payload["original_checkpoint_id"] == original_checkpoint["checkpoint_id"]
            assert imported_checkpoint["checkpoint_id"] != original_checkpoint["checkpoint_id"]
            assert imported_checkpoint["source_session_id"] == session_id
            assert imported_checkpoint["source_session_version"] == original_checkpoint["source_session_version"]
            assert imported_checkpoint["label"] == original_checkpoint["label"]
            assert imported_checkpoint["note"] == original_checkpoint["note"]
            assert _count_checkpoint_records(import_env_client) == checkpoint_count_before_import + 1

            listed_payload = _list_checkpoints(import_env_client, session_id)
            assert listed_payload["session_id"] == session_id
            assert [checkpoint["checkpoint_id"] for checkpoint in listed_payload["checkpoints"]] == [
                imported_checkpoint["checkpoint_id"]
            ]

            updated_payload = _update_checkpoint(
                import_env_client,
                session_id,
                imported_checkpoint["checkpoint_id"],
                label="导入后改名",
                note="导入后继续管理。",
                operator_id=KEEPER_ID,
            )
            assert updated_payload["checkpoint"]["label"] == "导入后改名"
            assert updated_payload["checkpoint"]["note"] == "导入后继续管理。"

            restore_payload = _restore_checkpoint(
                import_env_client,
                session_id,
                imported_checkpoint["checkpoint_id"],
            )
            restored_session_id = restore_payload["new_session_id"]
            continue_response = import_env_client.post(
                f"/sessions/{restored_session_id}/player-action",
                json={
                    "actor_id": "investigator-1",
                    "action_text": "我在导入后的 checkpoint 副本里继续调查前厅。",
                    "structured_action": {"type": "resume_from_imported_checkpoint"},
                },
            )
            assert continue_response.status_code == 202

            delete_payload = _delete_checkpoint(
                import_env_client,
                session_id,
                imported_checkpoint["checkpoint_id"],
            )
            assert delete_payload == {
                "message": "检查点已删除",
                "session_id": session_id,
                "checkpoint_id": imported_checkpoint["checkpoint_id"],
            }
            restore_missing_response = import_env_client.post(
                f"/sessions/{session_id}/checkpoints/{imported_checkpoint['checkpoint_id']}/restore",
                json={},
            )
            assert restore_missing_response.status_code == 404
            assert restore_missing_response.json()["detail"] == {
                "code": "session_checkpoint_not_found",
                "message": f"未找到检查点 {imported_checkpoint['checkpoint_id']}",
                "scope": "session_checkpoint_record",
                "session_id": session_id,
                "checkpoint_id": imported_checkpoint["checkpoint_id"],
                "source_session_id": session_id,
            }
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_imported_checkpoint_restore_inherits_import_warnings_for_missing_external_sources(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-checkpoint-export-import-cross-env"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
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
    original_session_id = start_response.json()["session_id"]
    _discover_note_and_queue_prompt(client, original_session_id)
    checkpoint = _create_checkpoint(
        client,
        original_session_id,
        label="跨环境导入检查点",
        note="验证 export/import 后 restore 仍继承 warning。",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    export_payload = _export_checkpoint(client, original_session_id, checkpoint["checkpoint_id"])

    import_env_client, run_dir = _make_cross_environment_client("checkpoint_import_missing_sources")
    try:
        with import_env_client:
            import_payload = _import_checkpoint_payload(import_env_client, export_payload)
            imported_checkpoint_id = import_payload["checkpoint"]["checkpoint_id"]

            restore_payload = _restore_checkpoint(
                import_env_client,
                original_session_id,
                imported_checkpoint_id,
            )

            warnings = restore_payload["warnings"]
            assert warnings
            assert all(warning["code"] == "missing_external_source" for warning in warnings)
            assert {
                "participant.imported_character_source_id",
                "character_state.import_source_id",
                "character_state.secret_state_refs",
            }.issubset({warning["scope"] for warning in warnings})
            assert any(warning.get("source_id") == source_id for warning in warnings)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_update_checkpoint_label_only_preserves_other_metadata_and_list_view(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="前厅保留点",
        note="旧备注",
        operator_id=KEEPER_ID,
    )["checkpoint"]

    update_payload = _update_checkpoint(
        client,
        session_id,
        checkpoint["checkpoint_id"],
        label="档案室前",
        operator_id=KEEPER_ID,
    )

    updated = update_payload["checkpoint"]
    assert update_payload["message"] == "检查点已更新"
    assert updated["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert updated["source_session_id"] == checkpoint["source_session_id"]
    assert updated["source_session_version"] == checkpoint["source_session_version"]
    assert _parse_iso_datetime(updated["created_at"]) == _parse_iso_datetime(checkpoint["created_at"])
    assert updated["created_by"] == checkpoint["created_by"]
    assert updated["label"] == "档案室前"
    assert updated["note"] == checkpoint["note"]

    listed = _list_checkpoints(client, session_id)["checkpoints"][0]
    assert listed["label"] == "档案室前"
    assert listed["note"] == "旧备注"


def test_update_checkpoint_note_only_preserves_other_metadata(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="发现纸条后",
        note="原始备注",
        operator_id=KEEPER_ID,
    )["checkpoint"]

    update_payload = _update_checkpoint(
        client,
        session_id,
        checkpoint["checkpoint_id"],
        note="只更新备注，不改名称。",
        operator_id=KEEPER_ID,
    )

    updated = update_payload["checkpoint"]
    assert updated["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert updated["label"] == checkpoint["label"]
    assert updated["note"] == "只更新备注，不改名称。"
    assert updated["source_session_id"] == checkpoint["source_session_id"]
    assert updated["source_session_version"] == checkpoint["source_session_version"]


def test_update_checkpoint_label_and_note_still_restores_original_snapshot(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, session_id)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="发现纸条后",
        note="原备注",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoint_snapshot = _get_snapshot(client, session_id)
    _move_to_archive(client, session_id)

    update_payload = _update_checkpoint(
        client,
        session_id,
        checkpoint["checkpoint_id"],
        label="改名后的分支点",
        note="改名且更新备注。",
        operator_id=KEEPER_ID,
    )
    assert update_payload["checkpoint"]["label"] == "改名后的分支点"
    assert update_payload["checkpoint"]["note"] == "改名且更新备注。"

    restore_payload = _restore_checkpoint(client, session_id, checkpoint["checkpoint_id"])
    restored_snapshot = _get_snapshot(client, restore_payload["new_session_id"])
    restored_event_texts = [event["text"] for event in restored_snapshot["timeline"]]

    assert restored_snapshot["current_scene"]["scene_id"] == checkpoint_snapshot["current_scene"]["scene_id"]
    assert "我顺着纸条指向前往档案室继续调查。" not in restored_event_texts


def test_delete_checkpoint_removes_it_without_affecting_original_or_restored_sessions(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _discover_note_and_queue_prompt(client, session_id)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="删除前分支点",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    source_snapshot_before_delete = _get_snapshot(client, session_id)
    restore_payload = _restore_checkpoint(client, session_id, checkpoint["checkpoint_id"])
    restored_session_id = restore_payload["new_session_id"]
    restored_snapshot_before_delete = _get_snapshot(client, restored_session_id)

    delete_response = client.delete(
        f"/sessions/{session_id}/checkpoints/{checkpoint['checkpoint_id']}",
    )

    assert delete_response.status_code == 200
    delete_payload = delete_response.json()
    assert delete_payload == {
        "message": "检查点已删除",
        "session_id": session_id,
        "checkpoint_id": checkpoint["checkpoint_id"],
    }
    assert _list_checkpoints(client, session_id)["checkpoints"] == []

    missing_restore_response = client.post(
        f"/sessions/{session_id}/checkpoints/{checkpoint['checkpoint_id']}/restore",
        json={},
    )
    assert missing_restore_response.status_code == 404
    assert missing_restore_response.json()["detail"] == {
        "code": "session_checkpoint_not_found",
        "message": f"未找到检查点 {checkpoint['checkpoint_id']}",
        "scope": "session_checkpoint_record",
        "session_id": session_id,
        "checkpoint_id": checkpoint["checkpoint_id"],
        "source_session_id": session_id,
    }

    assert _get_snapshot(client, session_id) == source_snapshot_before_delete
    assert _get_snapshot(client, restored_session_id) == restored_snapshot_before_delete

    player_action_response = client.post(
        f"/sessions/{restored_session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我在已经恢复出的分支里继续调查。",
            "structured_action": {"type": "continue_after_checkpoint_delete"},
        },
    )
    assert player_action_response.status_code == 202


def test_create_checkpoint_missing_session_returns_structured_404_without_creating_record(
    client: TestClient,
) -> None:
    checkpoint_count_before_request = _count_checkpoint_records(client)

    response = client.post(
        "/sessions/session-missing/checkpoints",
        json={
            "label": "不会创建",
            "operator_id": KEEPER_ID,
        },
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "session_checkpoint_session_not_found"
    assert detail["scope"] == "session_checkpoint_session"
    assert detail["session_id"] == "session-missing"
    assert detail["operator_id"] == KEEPER_ID
    assert isinstance(detail["message"], str) and detail["message"]
    assert _count_checkpoint_records(client) == checkpoint_count_before_request


def test_restore_checkpoint_missing_checkpoint_returns_structured_404_without_mutating_session(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    snapshot_before_restore = _get_snapshot(client, session_id)

    response = client.post(
        f"/sessions/{session_id}/checkpoints/checkpoint-missing/restore",
        json={},
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "session_checkpoint_not_found"
    assert detail["scope"] == "session_checkpoint_record"
    assert detail["session_id"] == session_id
    assert detail["checkpoint_id"] == "checkpoint-missing"
    assert detail["source_session_id"] == session_id
    assert isinstance(detail["message"], str) and detail["message"]
    assert _get_snapshot(client, session_id) == snapshot_before_restore


def test_update_checkpoint_missing_session_returns_structured_404_without_mutating_records(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="更新前检查点",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoints_before_failure = _list_checkpoints(client, session_id)

    response = client.patch(
        f"/sessions/session-missing/checkpoints/{checkpoint['checkpoint_id']}",
        json={"label": "不会成功"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "session_checkpoint_session_not_found",
        "message": "未找到会话 session-missing",
        "scope": "session_checkpoint_session",
        "session_id": "session-missing",
    }
    assert _list_checkpoints(client, session_id) == checkpoints_before_failure


def test_update_checkpoint_missing_checkpoint_returns_structured_404_without_mutating_records(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="保留点",
        note="原备注",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoints_before_failure = _list_checkpoints(client, session_id)

    response = client.patch(
        f"/sessions/{session_id}/checkpoints/checkpoint-missing",
        json={"label": "不会成功"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "session_checkpoint_not_found",
        "message": "未找到检查点 checkpoint-missing",
        "scope": "session_checkpoint_record",
        "session_id": session_id,
        "checkpoint_id": "checkpoint-missing",
        "source_session_id": session_id,
    }
    assert _list_checkpoints(client, session_id) == checkpoints_before_failure
    assert _list_checkpoints(client, session_id)["checkpoints"][0]["label"] == checkpoint["label"]


def test_update_checkpoint_without_mutations_returns_structured_400_without_mutating_record(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="保留点",
        note="原备注",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoints_before_failure = _list_checkpoints(client, session_id)

    response = client.patch(
        f"/sessions/{session_id}/checkpoints/{checkpoint['checkpoint_id']}",
        json={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "session_checkpoint_update_invalid",
        "message": "检查点更新请求至少要提供 label 或 note。",
        "scope": "session_checkpoint_update",
        "session_id": session_id,
        "checkpoint_id": checkpoint["checkpoint_id"],
        "source_session_id": session_id,
        "operator_id": KEEPER_ID,
    }
    assert _list_checkpoints(client, session_id) == checkpoints_before_failure


def test_delete_checkpoint_missing_session_returns_structured_404_without_mutating_records(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    checkpoint = _create_checkpoint(
        client,
        session_id,
        label="待删除检查点",
        operator_id=KEEPER_ID,
    )["checkpoint"]
    checkpoints_before_failure = _list_checkpoints(client, session_id)

    response = client.delete(
        f"/sessions/session-missing/checkpoints/{checkpoint['checkpoint_id']}",
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "session_checkpoint_session_not_found",
        "message": "未找到会话 session-missing",
        "scope": "session_checkpoint_session",
        "session_id": "session-missing",
    }
    assert _list_checkpoints(client, session_id) == checkpoints_before_failure


def test_delete_checkpoint_missing_checkpoint_returns_structured_404_without_mutating_records(
    client: TestClient,
) -> None:
    session_id = _start_snapshot_session(client)
    _create_checkpoint(
        client,
        session_id,
        label="仍应保留",
        operator_id=KEEPER_ID,
    )
    checkpoints_before_failure = _list_checkpoints(client, session_id)

    response = client.delete(
        f"/sessions/{session_id}/checkpoints/checkpoint-missing",
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "session_checkpoint_not_found",
        "message": "未找到检查点 checkpoint-missing",
        "scope": "session_checkpoint_record",
        "session_id": session_id,
        "checkpoint_id": "checkpoint-missing",
        "source_session_id": session_id,
    }
    assert _list_checkpoints(client, session_id) == checkpoints_before_failure


def test_import_checkpoint_invalid_payload_returns_structured_400_without_creating_record(
    client: TestClient,
) -> None:
    checkpoint_count_before_request = _count_checkpoint_records(client)
    invalid_payload = {
        "format_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint": {
            "checkpoint_id": "checkpoint-invalid",
            "source_session_id": "session-import-invalid",
            "source_session_version": 1,
            "label": "坏检查点",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_payload": {
                "session_id": "session-import-invalid",
                "participants": "oops",
            },
        },
    }

    response = client.post("/sessions/checkpoints/import", json=invalid_payload)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "session_checkpoint_import_invalid_payload"
    assert detail["message"] == "检查点导入载荷校验失败"
    assert detail["scope"] == "session_checkpoint_import_payload"
    assert detail["source_session_id"] == "session-import-invalid"
    assert detail["original_checkpoint_id"] == "checkpoint-invalid"
    assert isinstance(detail["errors"], list)
    assert _count_checkpoint_records(client) == checkpoint_count_before_request


def test_same_environment_import_with_existing_sources_keeps_warnings_empty(client: TestClient) -> None:
    source_id = "character-sheet-template-import-same-env"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
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
    original_session_id = start_response.json()["session_id"]

    _discover_note_and_queue_prompt(client, original_session_id)
    snapshot = _get_snapshot(client, original_session_id)

    import_response = _import_snapshot(client, snapshot)

    assert import_response["warnings"] == []


def test_cross_environment_import_returns_structured_missing_source_warnings_but_restores_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-import-cross-env"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
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
    original_session_id = start_response.json()["session_id"]

    _discover_note_and_queue_prompt(client, original_session_id)
    snapshot = _get_snapshot(client, original_session_id)
    original_secret_refs = list(snapshot["character_states"]["investigator-1"]["secret_state_refs"])
    assert any(ref.startswith(f"knowledge_source:{source_id}") for ref in original_secret_refs)

    import_env_client, run_dir = _make_cross_environment_client("missing_sources")
    try:
        with import_env_client:
            import_response = import_env_client.post("/sessions/import", json=snapshot)
            assert import_response.status_code == 201
            import_payload = import_response.json()

            warnings = import_payload["warnings"]
            assert warnings
            assert all("code" in warning for warning in warnings)
            assert all("message" in warning for warning in warnings)
            assert all("scope" in warning for warning in warnings)
            assert all(
                ("ref" in warning and warning["ref"]) or ("source_id" in warning and warning["source_id"])
                for warning in warnings
            )

            warning_scopes = {warning["scope"] for warning in warnings}
            assert "participant.imported_character_source_id" in warning_scopes
            assert "character_state.import_source_id" in warning_scopes
            assert "character_state.secret_state_refs" in warning_scopes
            assert all(warning["code"] == "missing_external_source" for warning in warnings)
            assert any(warning.get("source_id") == source_id for warning in warnings)
            assert any(
                warning["scope"] == "character_state.secret_state_refs"
                and warning.get("ref") == f"knowledge_source:{source_id}"
                for warning in warnings
            )

            imported_session_id = import_payload["new_session_id"]
            keeper_state = _get_keeper_state(import_env_client, imported_session_id)
            assert keeper_state["scenario"]["title"] == snapshot["scenario"]["title"]

            move_response = import_env_client.post(
                f"/sessions/{imported_session_id}/player-action",
                json={
                    "actor_id": "investigator-1",
                    "action_text": "我顺着纸条指向前往档案室继续调查。",
                    "structured_action": {"type": "move_to_archive"},
                    "effects": {
                        "scene_transitions": [{"scene_id": "scene.archive"}]
                    },
                },
            )
            assert move_response.status_code == 202

            imported_snapshot = _get_snapshot(import_env_client, imported_session_id)
            imported_participant = imported_snapshot["participants"][0]
            imported_character_state = imported_snapshot["character_states"]["investigator-1"]

            assert imported_participant["imported_character_source_id"] == source_id
            assert imported_character_state["import_source_id"] == source_id
            assert imported_character_state["secret_state_refs"] == original_secret_refs
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_cross_environment_import_allows_rules_grounding_to_degrade_without_crashing(
    client: TestClient,
) -> None:
    character_source_id = "character-sheet-template-import-grounding"
    rule_source_id = "cross-env-grounding-core"
    _import_character_sheet_source(client, source_id=character_source_id)
    _register_text_rule_source(
        client,
        source_id=rule_source_id,
        source_title_zh="跨环境 grounding 核心规则",
        content="# 图书馆使用\n图书馆使用用于在旧报纸、档案与馆藏中查阅资料。",
    )
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _snapshot_scenario(),
            "participants": [
                make_participant(
                    "investigator-1",
                    "占位调查员",
                    imported_character_source_id=character_source_id,
                )
            ],
        },
    )
    assert start_response.status_code == 201
    original_session_id = start_response.json()["session_id"]
    snapshot = _get_snapshot(client, original_session_id)

    source_env_action = client.post(
        f"/sessions/{original_session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我去图书馆查阅旧报纸。",
            "structured_action": {"type": "research"},
            "deterministic_resolution_required": True,
        },
    )
    assert source_env_action.status_code == 202
    source_env_grounding = source_env_action.json()["authoritative_event"]["rules_grounding"]
    assert source_env_grounding["query_text"] == "图书馆使用"
    assert source_env_grounding["deterministic_handoff_topic"] == "term:library_use"
    assert source_env_grounding["citations"]
    assert source_env_grounding["matched_topics"] == ["term:library_use"]

    import_env_client, run_dir = _make_cross_environment_client("grounding_missing_sources")
    try:
        with import_env_client:
            import_response = import_env_client.post("/sessions/import", json=snapshot)
            assert import_response.status_code == 201
            import_payload = import_response.json()
            assert import_payload["warnings"]

            imported_session_id = import_payload["new_session_id"]
            degraded_action = import_env_client.post(
                f"/sessions/{imported_session_id}/player-action",
                json={
                    "actor_id": "investigator-1",
                    "action_text": "我去图书馆查阅旧报纸。",
                    "structured_action": {"type": "research"},
                    "deterministic_resolution_required": True,
                },
            )
            assert degraded_action.status_code == 202

            degraded_payload = degraded_action.json()
            assert degraded_payload["grounding_degraded"] is True
            degraded_event = degraded_payload["authoritative_event"]
            degraded_grounding = degraded_event["rules_grounding"]
            assert degraded_grounding["query_text"] == "图书馆使用"
            assert degraded_grounding["deterministic_resolution_required"] is True
            assert degraded_grounding["deterministic_handoff_topic"] is None
            assert degraded_grounding["citations"] == []
            assert degraded_grounding["matched_topics"] == []
            assert (
                degraded_grounding["review_summary"]
                == "规则依据降级：当前环境缺少外部知识源，未命中可用规则依据。"
            )
            assert (
                degraded_event["structured_payload"]["rules_grounding"]["citations"] == []
            )
            assert (
                degraded_payload["authoritative_action"]["rules_grounding"]["deterministic_handoff_topic"]
                is None
            )

            keeper_state = _get_keeper_state(import_env_client, imported_session_id)
            investigator_state = _get_investigator_state(
                import_env_client,
                imported_session_id,
                "investigator-1",
            )
            imported_snapshot = _get_snapshot(import_env_client, imported_session_id)

            assert any(
                event["text"] == "我去图书馆查阅旧报纸。"
                for event in keeper_state["visible_events"]
            )
            assert any(
                event["text"] == "我去图书馆查阅旧报纸。"
                for event in investigator_state["visible_events"]
            )
            assert imported_snapshot["reviewed_actions"] == []
            assert imported_snapshot["timeline"][-1]["event_type"] == "player_action"
            assert imported_snapshot["authoritative_actions"][-1]["text"] == "我去图书馆查阅旧报纸。"
            assert imported_snapshot["authoritative_actions"][-1]["rules_grounding"]["citations"] == []
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_cross_environment_review_summary_surfaces_grounding_degraded_for_keeper_only(
    client: TestClient,
) -> None:
    character_source_id = "character-sheet-template-import-review-summary"
    degraded_summary = "规则依据降级：当前环境缺少外部知识源，未命中可用规则依据。"

    _import_character_sheet_source(client, source_id=character_source_id)
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _high_risk_grounding_review_scenario(),
            "participants": [
                make_participant(
                    "investigator-1",
                    "占位调查员",
                    imported_character_source_id=character_source_id,
                ),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    snapshot = _get_snapshot(client, start_response.json()["session_id"])

    import_env_client, run_dir = _make_cross_environment_client("review_summary_grounding_degraded")
    try:
        with import_env_client:
            import_response = import_env_client.post("/sessions/import", json=snapshot)
            assert import_response.status_code == 201
            assert import_response.json()["warnings"]

            imported_session_id = import_response.json()["new_session_id"]
            draft_response = import_env_client.post(
                f"/sessions/{imported_session_id}/player-action",
                json={
                    "actor_id": "ai-1",
                    "action_text": "我建议先去图书馆查阅旧报纸，再决定是否继续追踪旅店记录。",
                    "structured_action": {
                        "type": "research",
                        "risk_level": "high",
                        "requires_explicit_approval": True,
                    },
                    "rules_query_text": "图书馆使用",
                    "deterministic_resolution_required": True,
                },
            )
            assert draft_response.status_code == 202
            draft_payload = draft_response.json()
            assert draft_payload["grounding_degraded"] is True
            assert draft_payload["draft_action"]["review_status"] == "pending"
            assert (
                draft_payload["draft_action"]["rules_grounding"]["review_summary"]
                == degraded_summary
            )
            assert degraded_summary in draft_payload["draft_action"]["rationale_summary"]

            review_response = import_env_client.post(
                f"/sessions/{imported_session_id}/draft-actions/{draft_payload['draft_action']['draft_id']}/review",
                json={"reviewer_id": "keeper-1", "decision": "approve"},
            )
            assert review_response.status_code == 200
            review_payload = review_response.json()
            assert review_payload["grounding_degraded"] is True
            assert review_payload["reviewed_action"]["review_summary"] == degraded_summary
            assert (
                review_payload["reviewed_action"]["rules_grounding"]["review_summary"]
                == degraded_summary
            )
            assert review_payload["authoritative_action"]["review_summary"] == degraded_summary
            assert (
                review_payload["authoritative_action"]["rules_grounding"]["review_summary"]
                == degraded_summary
            )

            keeper_state = _get_keeper_state(import_env_client, imported_session_id)
            investigator_state = _get_investigator_state(
                import_env_client,
                imported_session_id,
                "investigator-1",
            )
            imported_snapshot = _get_snapshot(import_env_client, imported_session_id)

            reviewed_action = next(
                reviewed
                for reviewed in keeper_state["visible_reviewed_actions"]
                if reviewed["review_id"] == review_payload["reviewed_action"]["review_id"]
            )
            authoritative_action = next(
                action
                for action in keeper_state["visible_authoritative_actions"]
                if action["action_id"] == review_payload["authoritative_action"]["action_id"]
            )
            keeper_event = next(
                event
                for event in keeper_state["visible_events"]
                if event["event_id"] == review_payload["reviewed_action"]["canonical_event_id"]
            )
            investigator_event = next(
                event
                for event in investigator_state["visible_events"]
                if event["event_id"] == review_payload["reviewed_action"]["canonical_event_id"]
            )

            assert reviewed_action["review_summary"] == degraded_summary
            assert reviewed_action["rules_grounding"]["review_summary"] == degraded_summary
            assert authoritative_action["review_summary"] == degraded_summary
            assert authoritative_action["rules_grounding"]["review_summary"] == degraded_summary
            assert keeper_event["structured_payload"]["review_summary"] == degraded_summary
            assert (
                keeper_event["structured_payload"]["rules_grounding"]["review_summary"]
                == degraded_summary
            )

            assert investigator_state["visible_reviewed_actions"] == []
            assert investigator_state["visible_authoritative_actions"] == []
            assert "review_summary" not in investigator_event["structured_payload"]
            assert (
                "review_summary"
                not in investigator_event["structured_payload"]["rules_grounding"]
            )

            assert (
                imported_snapshot["reviewed_actions"][-1]["review_summary"]
                == degraded_summary
            )
            assert (
                imported_snapshot["authoritative_actions"][-1]["review_summary"]
                == degraded_summary
            )
            assert (
                imported_snapshot["timeline"][-1]["structured_payload"]["review_summary"]
                == degraded_summary
            )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_cross_environment_missing_character_import_source_refresh_fails_without_mutating_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-import-refresh-missing-cross-env"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
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
    original_session_id = start_response.json()["session_id"]

    manual_response = client.post(
        f"/sessions/{original_session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "actor_type": "investigator",
            "action_text": "手动记录调查中的体力消耗与随身物品变化。",
            "effects": {
                "character_stat_effects": [{"actor_id": "investigator-1", "hp_delta": -3}],
                "inventory_effects": [{"actor_id": "investigator-1", "add_items": ["现场证物"]}],
                "status_effects": [
                    {
                        "actor_id": "investigator-1",
                        "add_status_effects": ["受惊"],
                        "add_temporary_conditions": ["需要休整"],
                        "add_private_notes": ["手动确认：先保留当前会话状态。"],
                    }
                ],
            },
        },
    )
    assert manual_response.status_code == 202
    snapshot = _get_snapshot(client, original_session_id)
    original_character_state = snapshot["character_states"]["investigator-1"]
    expected_current_hit_points = original_character_state["current_hit_points"]

    assert snapshot["participants"][0]["imported_character_source_id"] == source_id
    assert original_character_state["import_source_id"] == source_id
    assert expected_current_hit_points >= 1
    assert "现场证物" in original_character_state["inventory"]
    assert "受惊" in original_character_state["status_effects"]
    assert "需要休整" in original_character_state["temporary_conditions"]
    assert "手动确认：先保留当前会话状态。" in original_character_state["private_notes"]
    assert any(ref.startswith(f"knowledge_source:{source_id}") for ref in original_character_state["secret_state_refs"])

    import_env_client, run_dir = _make_cross_environment_client("missing_character_refresh_source")
    try:
        with import_env_client:
            import_response = import_env_client.post("/sessions/import", json=snapshot)
            assert import_response.status_code == 201
            import_payload = import_response.json()
            assert import_payload["warnings"]
            assert {
                "participant.imported_character_source_id",
                "character_state.import_source_id",
            }.issubset({warning["scope"] for warning in import_payload["warnings"]})

            imported_session_id = import_payload["new_session_id"]
            imported_snapshot_before_refresh = _get_snapshot(import_env_client, imported_session_id)

            refresh_response = import_env_client.post(
                f"/sessions/{imported_session_id}/apply-character-import",
                json={
                    "operator_id": "keeper-1",
                    "actor_id": "investigator-1",
                    "source_id": source_id,
                    "sync_policy": "refresh_with_merge",
                },
            )
            assert refresh_response.status_code == 404
            refresh_detail = refresh_response.json()["detail"]
            assert refresh_detail == {
                "code": "character_import_source_not_found",
                "message": f"未找到角色导入源 {source_id}",
                "source_id": source_id,
                "session_id": imported_session_id,
                "actor_id": "investigator-1",
                "scope": "character_import_source",
            }

            imported_snapshot_after_refresh_failure = _get_snapshot(import_env_client, imported_session_id)
            assert imported_snapshot_after_refresh_failure == imported_snapshot_before_refresh

            imported_character_state = imported_snapshot_after_refresh_failure["character_states"]["investigator-1"]
            assert imported_snapshot_after_refresh_failure["participants"][0]["imported_character_source_id"] == source_id
            assert imported_character_state["import_source_id"] == source_id
            assert imported_character_state["secret_state_refs"] == original_character_state["secret_state_refs"]
            assert imported_character_state["current_hit_points"] == expected_current_hit_points
            assert "现场证物" in imported_character_state["inventory"]
            assert "受惊" in imported_character_state["status_effects"]
            assert "需要休整" in imported_character_state["temporary_conditions"]
            assert "手动确认：先保留当前会话状态。" in imported_character_state["private_notes"]

            keeper_state = _get_keeper_state(import_env_client, imported_session_id)
            investigator_state = _get_investigator_state(
                import_env_client,
                imported_session_id,
                "investigator-1",
            )

            assert (
                keeper_state["visible_character_states_by_actor"]["investigator-1"]["import_source_id"]
                == source_id
            )
            assert (
                investigator_state["own_character_state"]["import_source_id"]
                == source_id
            )
            assert (
                investigator_state["own_character_state"]["current_hit_points"]
                == expected_current_hit_points
            )
            assert "现场证物" in investigator_state["own_character_state"]["inventory"]
            assert investigator_state["visible_reviewed_actions"] == []

            continue_response = import_env_client.post(
                f"/sessions/{imported_session_id}/player-action",
                json={
                    "actor_id": "investigator-1",
                    "action_text": "我先核对现有角色状态，再继续调查前厅。",
                    "structured_action": {"type": "resume_investigation"},
                },
            )
            assert continue_response.status_code == 202

            continued_snapshot = _get_snapshot(import_env_client, imported_session_id)
            assert (
                continued_snapshot["character_states"]["investigator-1"]["current_hit_points"]
                == expected_current_hit_points
            )
            assert "现场证物" in continued_snapshot["character_states"]["investigator-1"]["inventory"]
            assert len(continued_snapshot["reviewed_actions"]) == len(
                imported_snapshot_after_refresh_failure["reviewed_actions"]
            )
            assert len(continued_snapshot["authoritative_actions"]) == (
                len(imported_snapshot_after_refresh_failure["authoritative_actions"]) + 1
            )
            assert continued_snapshot["timeline"][-1]["text"] == "我先核对现有角色状态，再继续调查前厅。"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_apply_character_import_missing_session_returns_structured_404_without_creating_session(
    client: TestClient,
) -> None:
    session_count_before_apply = _count_session_records(client)

    apply_response = client.post(
        "/sessions/missing-character-import-session/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "character-sheet-template-missing-session",
            "sync_policy": "refresh_with_merge",
        },
    )

    assert apply_response.status_code == 404
    assert apply_response.json()["detail"] == {
        "code": "character_import_session_not_found",
        "message": "未找到会话 missing-character-import-session",
        "source_id": "character-sheet-template-missing-session",
        "session_id": "missing-character-import-session",
        "actor_id": "investigator-1",
        "scope": "character_import_session",
    }
    assert _count_session_records(client) == session_count_before_apply


def test_apply_character_import_missing_extraction_returns_structured_400_without_mutating_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-missing-extraction"
    _register_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _snapshot_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]
    snapshot_before_apply = _get_snapshot(client, session_id)

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": source_id,
            "sync_policy": "refresh_with_merge",
        },
    )
    assert apply_response.status_code == 400
    assert apply_response.json()["detail"] == {
        "code": "character_import_missing_extraction",
        "message": f"知识源 {source_id} 尚未生成人物卡提取结果",
        "source_id": source_id,
        "session_id": session_id,
        "actor_id": "investigator-1",
        "scope": "character_import_source",
    }

    snapshot_after_apply_failure = _get_snapshot(client, session_id)
    assert snapshot_after_apply_failure == snapshot_before_apply


def test_apply_character_import_force_review_required_returns_structured_400_without_mutating_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-force-review-structured"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _snapshot_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]
    snapshot_before_apply = _get_snapshot(client, session_id)

    blocked_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": source_id,
            "sync_policy": "force_replace",
        },
    )
    assert blocked_response.status_code == 400
    assert blocked_response.json()["detail"] == {
        "code": "character_import_force_review_required",
        "message": "该导入仍需人工复核；如需强制覆盖会话状态，请显式启用 force_apply_manual_review",
        "source_id": source_id,
        "session_id": session_id,
        "actor_id": "investigator-1",
        "scope": "character_import_review",
    }

    snapshot_after_apply_failure = _get_snapshot(client, session_id)
    assert snapshot_after_apply_failure == snapshot_before_apply

    forced_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": source_id,
            "sync_policy": "force_replace",
            "force_apply_manual_review": True,
        },
    )
    assert forced_response.status_code == 200


def test_apply_character_import_not_supported_returns_structured_400_without_mutating_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-not-supported-structured"
    _import_character_sheet_source(client, source_id=source_id)
    client.app.state.session_service.knowledge_repository = None

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _snapshot_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]
    snapshot_before_apply = _get_snapshot(client, session_id)

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": source_id,
            "sync_policy": "refresh_with_merge",
        },
    )
    assert apply_response.status_code == 400
    assert apply_response.json()["detail"] == {
        "code": "character_import_not_supported",
        "message": "当前会话服务未启用角色导入知识仓库",
        "source_id": source_id,
        "session_id": session_id,
        "actor_id": "investigator-1",
        "scope": "character_import_support",
    }

    snapshot_after_apply_failure = _get_snapshot(client, session_id)
    assert snapshot_after_apply_failure == snapshot_before_apply


def test_apply_character_import_forbidden_returns_structured_403_without_mutating_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-forbidden-structured"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _snapshot_scenario(),
            "participants": [
                make_participant("investigator-1", "占位调查员"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]
    snapshot_before_apply = _get_snapshot(client, session_id)

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "investigator-2",
            "actor_id": "investigator-1",
            "source_id": source_id,
            "sync_policy": "refresh_with_merge",
        },
    )
    assert apply_response.status_code == 403
    assert apply_response.json()["detail"] == {
        "code": "character_import_operator_not_authorized",
        "message": "只有本局 KP 可以应用角色导入结果",
        "source_id": source_id,
        "session_id": session_id,
        "actor_id": "investigator-1",
        "operator_id": "investigator-2",
        "scope": "character_import_permission",
    }

    snapshot_after_apply_failure = _get_snapshot(client, session_id)
    assert snapshot_after_apply_failure == snapshot_before_apply


def test_apply_character_import_state_conflict_returns_structured_409_without_mutating_session(
    client: TestClient,
) -> None:
    source_id = "character-sheet-template-conflict-structured"
    _import_character_sheet_source(client, source_id=source_id)
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _snapshot_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]
    snapshot_before_apply = _get_snapshot(client, session_id)

    def _conflicting_save_session(session, *, expected_version, reason, language):
        raise ConflictError("会话状态版本冲突，请重新加载后再试")

    client.app.state.session_service._save_session = _conflicting_save_session

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": source_id,
            "sync_policy": "refresh_with_merge",
        },
    )
    assert apply_response.status_code == 409
    assert apply_response.json()["detail"] == {
        "code": "character_import_state_conflict",
        "message": "会话状态版本冲突，请重新加载后再试",
        "source_id": source_id,
        "session_id": session_id,
        "actor_id": "investigator-1",
        "scope": "character_import_state",
    }

    snapshot_after_apply_failure = _get_snapshot(client, session_id)
    assert snapshot_after_apply_failure == snapshot_before_apply
