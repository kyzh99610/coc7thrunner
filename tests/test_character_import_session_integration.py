from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from coc_runner.infrastructure.models import SessionRecord

from tests.helpers import make_participant, make_scenario


UPLOADED_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "coc7th rules and templates"
UPLOADED_TEMPLATE_SAMPLE_DIR = UPLOADED_TEMPLATE_DIR / "sample templates"


def _register_integrated_workbook_source(
    client: TestClient,
    *,
    source_id: str,
) -> None:
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


def _import_integrated_workbook(
    client: TestClient,
    *,
    source_id: str,
) -> dict:
    _register_integrated_workbook_source(client, source_id=source_id)
    response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": source_id},
    )
    assert response.status_code == 200
    return response.json()


def _count_session_records(client: TestClient) -> int:
    repository = client.app.state.session_service.repository
    with repository.session_factory() as db:
        return db.execute(select(func.count()).select_from(SessionRecord)).scalar_one()


def test_start_session_initializes_character_state_from_imported_workbook(
    client: TestClient,
) -> None:
    imported_payload = _import_integrated_workbook(
        client,
        source_id="character-sheet-template-session-init",
    )
    extraction = imported_payload["extraction"]

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [
                make_participant(
                    "investigator-1",
                    "占位调查员",
                    imported_character_source_id="character-sheet-template-session-init",
                )
            ],
        },
    )
    assert start_response.status_code == 201
    keeper_view = start_response.json()["keeper_view"]

    participant_summary = keeper_view["participants"][0]
    character_state = keeper_view["visible_character_states_by_actor"]["investigator-1"]

    assert participant_summary["display_name"] == extraction["investigator_name"]
    assert participant_summary["character"]["name"] == extraction["investigator_name"]
    assert participant_summary["character"]["occupation"] == extraction["occupation"]
    assert character_state["current_hit_points"] == extraction["derived_stats"]["hp"]
    assert character_state["current_magic_points"] == extraction["derived_stats"]["mp"]
    assert character_state["current_sanity"] == extraction["derived_stats"]["san"]
    assert character_state["core_stat_baseline"] == extraction["core_stats"]
    assert character_state["skill_baseline"] == extraction["skills"]
    assert character_state["inventory"] == extraction["starting_inventory"]
    assert character_state["import_source_id"] == "character-sheet-template-session-init"
    assert (
        character_state["import_template_profile"]
        == "coc7th_integrated_workbook_v1"
    )
    assert character_state["import_manual_review_required"] is True
    assert "knowledge_source:character-sheet-template-session-init" in character_state["secret_state_refs"]
    assert (
        "knowledge_source:character-sheet-template-session-init:secrets"
        in character_state["secret_state_refs"]
    )
    assert character_state["import_review_pending"] is True
    assert character_state["last_import_sync_policy"] == "initialize_if_missing"
    assert character_state["last_import_sync_report"]["manual_review_required"] is True
    assert character_state["last_import_sync_report"]["review_pending"] is True
    assert "participant.character.attributes" in character_state["last_import_sync_report"]["applied_fields"]
    assert "character_state.status_effects" in character_state["last_import_sync_report"]["skipped_fields"]
    assert (
        character_state["last_import_sync_report"]["key_field_provenance"]["investigator_name"]["source_anchor"]
        == "E3"
    )
    assert any(
        (extraction["campaign_notes"] or "") in note
        for note in character_state["private_notes"]
    )


def test_start_session_duplicate_participants_returns_structured_400_without_creating_session(
    client: TestClient,
) -> None:
    scenario = make_scenario()
    scenario["scenario_id"] = "scenario-start-duplicate"
    session_count_before_start = _count_session_records(client)

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": scenario,
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("investigator-1", "周岚"),
            ],
        },
    )

    assert start_response.status_code == 400
    detail = start_response.json()["detail"]
    assert detail["code"] == "session_start_invalid"
    assert detail["message"] == "会话初始化校验失败"
    assert detail["scope"] == "session_start_payload"
    assert detail["scenario_id"] == "scenario-start-duplicate"
    assert detail["participant_count"] == 2
    assert any(
        error["loc"] == []
        and error["message"] == "Value error, participant actor_ids must be unique"
        and error["type"] == "value_error"
        for error in detail["errors"]
    )
    session_count_after_start = _count_session_records(client)
    assert session_count_after_start == session_count_before_start


def test_start_session_missing_import_source_returns_structured_404_without_creating_session(
    client: TestClient,
) -> None:
    scenario = make_scenario()
    scenario["scenario_id"] = "scenario-start-missing-source"
    session_count_before_start = _count_session_records(client)

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": scenario,
            "participants": [
                make_participant(
                    "investigator-1",
                    "占位调查员",
                    imported_character_source_id="character-sheet-template-start-missing-source",
                )
            ],
        },
    )

    assert start_response.status_code == 404
    assert start_response.json()["detail"] == {
        "code": "session_start_character_import_source_not_found",
        "message": "未找到角色导入源 character-sheet-template-start-missing-source",
        "scope": "session_start_character_import",
        "scenario_id": "scenario-start-missing-source",
        "participant_count": 1,
        "actor_id": "investigator-1",
        "source_id": "character-sheet-template-start-missing-source",
    }
    session_count_after_start = _count_session_records(client)
    assert session_count_after_start == session_count_before_start


def test_start_session_missing_import_extraction_returns_structured_400_without_creating_session(
    client: TestClient,
) -> None:
    _register_integrated_workbook_source(
        client,
        source_id="character-sheet-template-start-missing-extraction",
    )
    scenario = make_scenario()
    scenario["scenario_id"] = "scenario-start-missing-extraction"
    session_count_before_start = _count_session_records(client)

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": scenario,
            "participants": [
                make_participant(
                    "investigator-1",
                    "占位调查员",
                    imported_character_source_id="character-sheet-template-start-missing-extraction",
                )
            ],
        },
    )

    assert start_response.status_code == 400
    assert start_response.json()["detail"] == {
        "code": "session_start_character_import_invalid",
        "message": "知识源 character-sheet-template-start-missing-extraction 尚未生成人物卡提取结果",
        "scope": "session_start_character_import",
        "scenario_id": "scenario-start-missing-extraction",
        "participant_count": 1,
        "actor_id": "investigator-1",
        "source_id": "character-sheet-template-start-missing-extraction",
    }
    session_count_after_start = _count_session_records(client)
    assert session_count_after_start == session_count_before_start


def test_apply_character_import_refreshes_existing_session_character_state(
    client: TestClient,
) -> None:
    imported_payload = _import_integrated_workbook(
        client,
        source_id="character-sheet-template-session-refresh",
    )
    extraction = imported_payload["extraction"]

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "character-sheet-template-session-refresh",
            "sync_policy": "force_replace",
            "force_apply_manual_review": True,
        },
    )
    assert apply_response.status_code == 200
    payload = apply_response.json()
    applied_state = payload["character_state"]
    sync_report = payload["sync_report"]

    assert applied_state["current_hit_points"] == extraction["derived_stats"]["hp"]
    assert applied_state["current_magic_points"] == extraction["derived_stats"]["mp"]
    assert applied_state["current_sanity"] == extraction["derived_stats"]["san"]
    assert applied_state["core_stat_baseline"]["strength"] == extraction["core_stats"]["strength"]
    assert applied_state["skill_baseline"]["侦查"] == extraction["skills"]["侦查"]
    assert applied_state["inventory"] == extraction["starting_inventory"]
    assert applied_state["import_source_id"] == "character-sheet-template-session-refresh"
    assert applied_state["import_manual_review_required"] is True
    assert applied_state["import_review_pending"] is False
    assert sync_report["policy"] == "force_replace"
    assert sync_report["review_pending"] is False
    assert "character_state.current_hit_points" in sync_report["applied_fields"]
    assert "character_state.status_effects" in sync_report["skipped_fields"]
    assert "occupation" in applied_state["last_import_sync_report"]["key_field_provenance"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert keeper_state.status_code == 200
    keeper_payload = keeper_state.json()
    participant_summary = keeper_payload["participants"][0]
    character_state = keeper_payload["visible_character_states_by_actor"]["investigator-1"]

    assert participant_summary["display_name"] == extraction["investigator_name"]
    assert participant_summary["character"]["name"] == extraction["investigator_name"]
    assert participant_summary["character"]["occupation"] == extraction["occupation"]
    assert character_state["current_hit_points"] == extraction["derived_stats"]["hp"]
    assert character_state["inventory"] == extraction["starting_inventory"]
    assert any(
        note.startswith("导入背景：") or note.startswith("导入备注：")
        for note in character_state["private_notes"]
    )

    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "investigator", "viewer_id": "investigator-1"},
    )
    assert investigator_state.status_code == 200
    assert (
        investigator_state.json()["own_character_state"]["import_source_id"]
        == "character-sheet-template-session-refresh"
    )


def test_force_replace_requires_explicit_manual_review_override(
    client: TestClient,
) -> None:
    _import_integrated_workbook(
        client,
        source_id="character-sheet-template-force-review",
    )
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    blocked_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "character-sheet-template-force-review",
            "sync_policy": "force_replace",
        },
    )
    assert blocked_response.status_code == 400
    assert blocked_response.json()["detail"] == {
        "code": "character_import_force_review_required",
        "message": "该导入仍需人工复核；如需强制覆盖会话状态，请显式启用 force_apply_manual_review",
        "source_id": "character-sheet-template-force-review",
        "session_id": session_id,
        "actor_id": "investigator-1",
        "scope": "character_import_review",
    }

    forced_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "character-sheet-template-force-review",
            "sync_policy": "force_replace",
            "force_apply_manual_review": True,
        },
    )
    assert forced_response.status_code == 200
    forced_payload = forced_response.json()
    assert forced_payload["character_state"]["import_review_pending"] is False
    assert forced_payload["sync_report"]["review_pending"] is False
    assert forced_payload["sync_report"]["policy"] == "force_replace"


def test_refresh_static_fields_only_preserves_session_authoritative_mutable_state(
    client: TestClient,
) -> None:
    _import_integrated_workbook(
        client,
        source_id="character-sheet-template-static-refresh",
    )
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    manual_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "actor_type": "investigator",
            "action_text": "手动调整会话状态",
            "effects": {
                "character_stat_effects": [{"actor_id": "investigator-1", "hp_delta": -4}],
                "inventory_effects": [{"actor_id": "investigator-1", "add_items": ["现场证物"]}],
                "status_effects": [{"actor_id": "investigator-1", "add_private_notes": ["手动备注"]}],
            },
        },
    )
    assert manual_response.status_code == 202

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "character-sheet-template-static-refresh",
            "sync_policy": "refresh_static_fields_only",
        },
    )
    assert apply_response.status_code == 200
    payload = apply_response.json()
    state = payload["character_state"]
    sync_report = payload["sync_report"]

    assert state["current_hit_points"] == 7
    assert state["inventory"] == ["现场证物"]
    assert "手动备注" in state["private_notes"]
    assert state["import_source_id"] == "character-sheet-template-static-refresh"
    assert sync_report["policy"] == "refresh_static_fields_only"
    assert "character_state.current_hit_points" in sync_report["skipped_fields"]
    assert "character_state.inventory" in sync_report["skipped_fields"]
    assert "participant.character.name" in sync_report["applied_fields"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert keeper_state.status_code == 200
    participant_summary = keeper_state.json()["participants"][0]
    assert participant_summary["display_name"] == "布鲁斯·维恩"


def test_refresh_with_merge_keeps_current_stats_and_merges_import_inventory(
    client: TestClient,
) -> None:
    imported_payload = _import_integrated_workbook(
        client,
        source_id="character-sheet-template-merge-refresh",
    )
    extraction = imported_payload["extraction"]
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "占位调查员")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    manual_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "actor_type": "investigator",
            "action_text": "手动调整会话状态",
            "effects": {
                "character_stat_effects": [{"actor_id": "investigator-1", "hp_delta": -2}],
                "inventory_effects": [{"actor_id": "investigator-1", "add_items": ["现场证物"]}],
            },
        },
    )
    assert manual_response.status_code == 202

    apply_response = client.post(
        f"/sessions/{session_id}/apply-character-import",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "source_id": "character-sheet-template-merge-refresh",
            "sync_policy": "refresh_with_merge",
        },
    )
    assert apply_response.status_code == 200
    payload = apply_response.json()
    state = payload["character_state"]
    sync_report = payload["sync_report"]

    assert state["current_hit_points"] == 9
    assert "现场证物" in state["inventory"]
    for item in extraction["starting_inventory"]:
        assert item in state["inventory"]
    assert sync_report["policy"] == "refresh_with_merge"
    assert "character_state.current_hit_points" in sync_report["skipped_fields"]
    assert "character_state.inventory" in sync_report["applied_fields"]
