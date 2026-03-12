from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from coc_runner.config import Settings
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


def _import_snapshot(client: TestClient, snapshot: dict) -> dict:
    response = client.post("/sessions/import", json=snapshot)
    assert response.status_code == 201
    return response.json()


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
