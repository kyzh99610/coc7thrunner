from __future__ import annotations

from fastapi.testclient import TestClient

from coc_runner.domain.scenario_examples import (
    blackout_clinic_payload,
    midnight_archive_payload,
    whispering_guesthouse_payload,
)
from tests.helpers import make_participant


def _register_rule_source(
    client: TestClient,
    *,
    source_id: str,
    title: str,
    content: str,
) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": "rulebook",
            "source_format": "markdown",
            "source_title_zh": title,
            "document_identity": source_id,
            "default_priority": 50,
            "is_authoritative": True,
        },
    )
    assert register_response.status_code == 201
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert ingest_response.status_code == 200


def test_whispering_guesthouse_authored_scenario_supports_playable_keeper_flow(
    client: TestClient,
) -> None:
    _register_rule_source(
        client,
        source_id="authored-spot-hidden",
        title="侦查规则",
        content="# 侦查\n侦查用于发现隐藏线索、门槛磨损和被掩盖的痕迹。",
    )
    _register_rule_source(
        client,
        source_id="authored-sanity",
        title="理智检定规则",
        content="# 理智检定\n当低语、怪异符号或重大冲击出现时，KP应人工确认是否立刻要求理智检定。",
    )

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": whispering_guesthouse_payload(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    keeper_initial = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_initial = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    keeper_scenes_initial = {scene["scene_id"]: scene for scene in keeper_initial["scenario"]["scenes"]}
    investigator_scene_ids_initial = {scene["scene_id"] for scene in investigator_initial["scenario"]["scenes"]}
    assert keeper_initial["current_scene"]["scene_id"] == "scene.guesthouse_lobby"
    assert keeper_scenes_initial["scene.guesthouse_lobby"]["revealed"] is True
    assert keeper_scenes_initial["scene.guesthouse_office"]["revealed"] is False
    assert keeper_initial["progress_state"]["npc_attitudes"]["npc.innkeeper"] == "guarded"
    assert keeper_initial["keeper_workflow"]["unresolved_objectives"][0]["objective_id"] == "objective.lobby.observe_keeper"
    assert investigator_scene_ids_initial == {"scene.guesthouse_lobby"}
    assert investigator_initial["scenario"]["scenes"][0]["keeper_notes"] == []

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
    lobby_action_payload = lobby_action.json()["authoritative_action"]
    assert any(
        transition["beat_id"] == "beat.lobby_pressure" and transition["transition"] == "completed"
        for transition in lobby_action_payload["applied_beat_transitions"]
    )
    assert any(
        transition["beat_id"] == "beat.office_records" and transition["transition"] == "current"
        for transition in lobby_action_payload["applied_beat_transitions"]
    )

    after_lobby = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    after_lobby_investigator = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    keeper_scene_ids_after_lobby = {
        scene["scene_id"]: scene for scene in after_lobby["scenario"]["scenes"]
    }
    assert after_lobby["current_scene"]["scene_id"] == "scene.guesthouse_office"
    assert keeper_scene_ids_after_lobby["scene.guesthouse_office"]["revealed"] is True
    assert {scene["scene_id"] for scene in after_lobby_investigator["scenario"]["scenes"]} == {
        "scene.guesthouse_lobby",
        "scene.guesthouse_office",
    }
    assert any(
        objective["objective_id"] == "objective.office.find_records"
        for objective in after_lobby["keeper_workflow"]["unresolved_objectives"]
    )
    assert any(
        prompt["category"] == "npc_reaction"
        and prompt["scene_id"] == "scene.guesthouse_lobby"
        for prompt in after_lobby["keeper_workflow"]["active_prompts"]
    )

    office_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我翻看账房残页并沿记录找到被封死的地窖门。",
            "structured_action": {"type": "read_ledger"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene.guesthouse_cellar"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue.office_ledger",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "read_ledger",
                    }
                ],
            },
        },
    )
    assert office_action.status_code == 202
    office_action_payload = office_action.json()["authoritative_action"]
    assert any(
        transition["beat_id"] == "beat.office_records" and transition["transition"] == "completed"
        for transition in office_action_payload["applied_beat_transitions"]
    )
    assert any(
        transition["beat_id"] == "beat.cellar_entry" and transition["transition"] == "current"
        for transition in office_action_payload["applied_beat_transitions"]
    )

    before_draft_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    transition_count_before_draft = len(before_draft_review["progress_state"]["transition_history"])
    assert before_draft_review["current_scene"]["scene_id"] == "scene.guesthouse_cellar"
    assert any(
        objective["objective_id"] == "objective.cellar.assess_whispers"
        for objective in before_draft_review["keeper_workflow"]["unresolved_objectives"]
    )
    assert before_draft_review["progress_state"]["npc_attitudes"]["npc.innkeeper"] == "defensive"

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议沿门槛和锁链继续侦查低语来源。",
            "structured_action": {"type": "investigate_search"},
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert draft_response.status_code == 202
    draft_payload = draft_response.json()["draft_action"]
    assert draft_payload["review_status"] == "pending"

    after_draft = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert after_draft["progress_state"]["current_beat"] == "beat.cellar_entry"
    assert len(after_draft["progress_state"]["transition_history"]) == transition_count_before_draft
    assert "clue.cellar_sigil" not in after_draft["progress_state"]["activated_fail_forward_clues"]

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_payload['draft_id']}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 200
    approved_action = review_response.json()["authoritative_action"]
    assert any(
        transition["beat_id"] == "beat.cellar_entry"
        and transition["transition"] == "fail_forward_activated"
        for transition in approved_action["applied_beat_transitions"]
    )
    assert any(
        transition["beat_id"] == "beat.sanity_review"
        and transition["transition"] == "current"
        for transition in approved_action["applied_beat_transitions"]
    )

    keeper_final = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_final = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    final_clues = {clue["clue_id"]: clue for clue in investigator_final["scenario"]["clues"]}
    assert final_clues["clue.cellar_sigil"]["status"] == "shared_with_party"
    assert "心神不宁" in investigator_final["own_character_state"]["temporary_conditions"]
    assert keeper_final["progress_state"]["current_beat"] == "beat.sanity_review"
    assert "clue.cellar_sigil" in keeper_final["progress_state"]["activated_fail_forward_clues"]
    assert any(
        prompt["category"] == "sanity_review"
        and prompt["trigger_reason"] == "核心线索伴随明显精神压力"
        and prompt["source_action_id"] == approved_action["action_id"]
        for prompt in keeper_final["keeper_workflow"]["active_prompts"]
    )
    assert any(
        objective["objective_id"] == "beat:beat.sanity_review"
        and objective["source_action_id"] == approved_action["action_id"]
        for objective in keeper_final["keeper_workflow"]["unresolved_objectives"]
    )
    assert any(
        transition["trigger_action_id"] == approved_action["action_id"]
        and transition["reason"] == "核心线索触发失手前进，避免单点卡死"
        for transition in keeper_final["progress_state"]["transition_history"]
    )
    assert any(
        transition["trigger_action_id"] == approved_action["action_id"]
        and "queue_kp_prompt:sanity_review" in transition["consequence_refs"]
        for transition in keeper_final["progress_state"]["transition_history"]
        if transition["beat_id"] == "beat.cellar_entry"
    )


def test_midnight_archive_authored_scenario_uses_scene_ids_and_fail_forward_cleanly(
    client: TestClient,
) -> None:
    _register_rule_source(
        client,
        source_id="archive-spot-hidden",
        title="侦查规则",
        content="# 侦查\n侦查用于发现被掩盖的痕迹、烧灼痕迹与异常温度变化。",
    )

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": midnight_archive_payload(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    initial_keeper = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert initial_keeper["current_scene"]["scene_id"] == "scene.archive_reading_room"
    assert initial_keeper["keeper_workflow"]["unresolved_objectives"][0]["objective_id"] == (
        "objective.archive.review_catalog"
    )

    catalog_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我翻出夜间借阅目录，并顺着备注走向地下楼梯间。",
            "structured_action": {"type": "review_catalog"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene.archive_basement_stairs"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue.burned_memo",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "review_catalog",
                    }
                ],
            },
        },
    )
    assert catalog_action.status_code == 202
    catalog_transitions = catalog_action.json()["authoritative_action"]["applied_beat_transitions"]
    assert any(
        transition["beat_id"] == "beat.archive_review_catalog"
        and transition["transition"] == "completed"
        for transition in catalog_transitions
    )
    assert any(
        transition["beat_id"] == "beat.archive_inspect_stairs"
        and transition["transition"] == "current"
        for transition in catalog_transitions
    )

    search_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我沿着扶手和台阶边缘搜索异常灼痕。",
            "structured_action": {"type": "investigate_search"},
            "rules_query_text": "侦察能发现隐藏痕迹吗",
            "deterministic_resolution_required": True,
        },
    )
    assert search_action.status_code == 202
    search_payload = search_action.json()["authoritative_action"]
    assert any(
        transition["beat_id"] == "beat.archive_inspect_stairs"
        and transition["transition"] == "fail_forward_activated"
        for transition in search_payload["applied_beat_transitions"]
    )
    assert any(
        transition["beat_id"] == "beat.archive_decide_descent"
        and transition["transition"] == "current"
        for transition in search_payload["applied_beat_transitions"]
    )

    final_keeper = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    final_investigator = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    assert final_keeper["current_scene"]["scene_id"] == "scene.archive_basement_stairs"
    assert final_keeper["progress_state"]["current_beat"] == "beat.archive_decide_descent"
    assert "clue.burn_mark" in final_keeper["progress_state"]["activated_fail_forward_clues"]
    assert any(
        prompt["category"] == "hazard_review"
        and prompt["scene_id"] == "scene.archive_basement_stairs"
        for prompt in final_keeper["keeper_workflow"]["active_prompts"]
    )
    assert "余悸" in final_investigator["own_character_state"]["temporary_conditions"]


def test_blackout_clinic_authored_scenario_supports_prompt_priority_and_workflow_summary(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": blackout_clinic_payload(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    initial_keeper = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert initial_keeper["current_scene"]["scene_id"] == "scene.clinic_reception"
    assert initial_keeper["keeper_workflow"]["summary"]["unresolved_objective_count"] == 1

    intake_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我翻开夜班登记簿，确认最后进入病历室的人，并立刻推门去病历室。",
            "structured_action": {"type": "review_intake_log"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene.clinic_records"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue.intake_log",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "review_intake_log",
                    }
                ],
            },
        },
    )
    assert intake_action.status_code == 202
    action_payload = intake_action.json()["authoritative_action"]
    assert any(
        transition["beat_id"] == "beat.clinic_review_intake"
        and transition["transition"] == "completed"
        for transition in action_payload["applied_beat_transitions"]
    )
    assert any(
        transition["beat_id"] == "beat.clinic_inspect_records"
        and transition["transition"] == "current"
        for transition in action_payload["applied_beat_transitions"]
    )

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    prompt = keeper_state["keeper_workflow"]["active_prompts"][0]
    assert prompt["category"] == "npc_pressure"
    assert prompt["priority"] == "high"
    assert prompt["assigned_to"] == "keeper-1"
    assert keeper_state["keeper_workflow"]["summary"]["active_prompt_count"] == 1
    assert any(
        "指派：keeper-1" in line
        for line in keeper_state["keeper_workflow"]["summary"]["summary_lines"]
    )
    assert any(
        objective["text"] == "确认停电前最后接触病历室的人是谁"
        for objective in keeper_state["keeper_workflow"]["summary"]["recently_completed_objectives"]
    )
    assert investigator_state["scenario"]["scenes"][-1]["scene_id"] == "scene.clinic_records"
    investigator_clues = {clue["clue_id"]: clue for clue in investigator_state["scenario"]["clues"]}
    assert investigator_clues["clue.cabinet_key"]["status"] == "private_to_actor"
