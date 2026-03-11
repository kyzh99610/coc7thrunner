from __future__ import annotations

from fastapi.testclient import TestClient

from coc_runner.domain.scenario_examples import blackout_clinic_payload
from tests.helpers import make_participant, make_scenario


KEEPER_ACTOR_ID = "keeper-1"


def _register_smoke_rule_source(client: TestClient, *, source_id: str) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": "rulebook",
            "source_format": "markdown",
            "source_title_zh": "最小试玩烟雾规则",
            "document_identity": source_id,
            "default_priority": 40,
            "is_authoritative": True,
        },
    )
    assert register_response.status_code == 201
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": source_id,
            "content": (
                "# 侦查\n侦查用于发现隐藏纸条、门后低语与可疑水痕。\n\n"
                "# 理智检定\n当调查员确认房内真相并承受明显精神压力时，KP应保留人工审阅。"
            ),
        },
    )
    assert ingest_response.status_code == 200


def _minimal_whispering_guesthouse_smoke_scenario() -> dict:
    return make_scenario(
        start_scene_id="scene_inn_lobby",
        clues=[
            {
                "clue_id": "clue_whisper_note",
                "title": "染潮纸条",
                "text": "纸条上反复提到二楼走廊尽头的门后低语。",
                "visibility_scope": "kp_only",
            },
            {
                "clue_id": "clue_room_whisper",
                "title": "门后低语",
                "text": "门板后传来的低语像是在重复同一段含混警告。",
                "visibility_scope": "kp_only",
            },
            {
                "clue_id": "clue_log_fragment",
                "title": "破碎日志",
                "text": "残缺日志记录着住客被潮湿低语逼疯前的最后几夜。",
                "visibility_scope": "kp_only",
            },
        ],
        scenes=[
            {
                "scene_id": "scene_inn_lobby",
                "title": "旅店前厅",
                "summary": "柜台、湿伞架与煤气灯让前厅始终带着潮气。",
                "revealed": True,
                "linked_clue_ids": ["clue_whisper_note"],
                "scene_objectives": [
                    {
                        "objective_id": "obj_find_note",
                        "text": "确认前厅里是否有被水汽浸透的关键线索",
                        "beat_id": "beat_find_note",
                    }
                ],
                "keeper_notes": ["若调查员细查柜台下方，可引出染潮纸条。"],
            },
            {
                "scene_id": "scene_second_floor_corridor",
                "title": "二楼走廊",
                "summary": "走廊木板潮得发软，尽头的门后像有人贴着门缝低语。",
                "revealed": False,
                "linked_clue_ids": ["clue_room_whisper"],
                "scene_objectives": [
                    {
                        "objective_id": "obj_reach_corridor",
                        "text": "确认门后低语是否属实",
                        "beat_id": "beat_reach_corridor",
                    }
                ],
                "keeper_notes": ["若调查员在此聆听过久，应提示后续理智审阅。"],
            },
            {
                "scene_id": "scene_locked_guest_room",
                "title": "上锁客房",
                "summary": "门内积着潮水痕，破碎日志与水渍把真相钉死在房中。",
                "revealed": False,
                "linked_clue_ids": ["clue_log_fragment"],
                "scene_objectives": [
                    {
                        "objective_id": "obj_room_truth",
                        "text": "确认客房里日志与水痕指向的真相",
                        "beat_id": "beat_room_truth",
                    }
                ],
                "keeper_notes": ["客房真相应伴随明显精神压力与人工审阅提示。"],
            },
        ],
        beats=[
            {
                "beat_id": "beat_find_note",
                "title": "发现染潮纸条",
                "start_unlocked": True,
                "complete_conditions": {
                    "clue_discovered": {"clue_id": "clue_whisper_note"}
                },
                "consequences": [
                    {
                        "reveal_scenes": [{"scene_id": "scene_second_floor_corridor"}],
                        "queue_kp_prompts": [
                            {
                                "prompt_text": "KP：染潮纸条已把线索指向二楼走廊，请准备引导下一拍调查。",
                                "category": "scene_followup",
                                "scene_id": "scene_inn_lobby",
                                "reason": "前厅线索明确指向二楼。",
                            }
                        ],
                        "mark_scene_objectives_complete": [{"objective_id": "obj_find_note"}],
                    }
                ],
                "next_beats": ["beat_reach_corridor"],
            },
            {
                "beat_id": "beat_reach_corridor",
                "title": "确认门后低语",
                "complete_conditions": {
                    "all_of": [
                        {"current_scene_in": {"scene_ids": ["scene_second_floor_corridor"]}},
                        {"clue_discovered": {"clue_id": "clue_room_whisper"}},
                    ]
                },
                "consequences": [
                    {
                        "reveal_scenes": [{"scene_id": "scene_locked_guest_room"}],
                        "queue_kp_prompts": [
                            {
                                "prompt_text": "KP：门后低语已得到确认，请保留一次理智相关人工审阅。",
                                "category": "sanity_review",
                                "scene_id": "scene_second_floor_corridor",
                                "reason": "门后低语带来明确精神压力。",
                            }
                        ],
                        "mark_scene_objectives_complete": [{"objective_id": "obj_reach_corridor"}],
                    }
                ],
                "next_beats": ["beat_room_truth"],
            },
            {
                "beat_id": "beat_room_truth",
                "title": "确认房内真相",
                "complete_conditions": {
                    "all_of": [
                        {"current_scene_in": {"scene_ids": ["scene_locked_guest_room"]}},
                        {"clue_discovered": {"clue_id": "clue_log_fragment"}},
                    ]
                },
                "consequences": [
                    {
                        "mark_scene_objectives_complete": [{"objective_id": "obj_room_truth"}],
                    }
                ],
            },
        ],
    )


def _beat_map(state_payload: dict) -> dict[str, dict]:
    return {beat["beat_id"]: beat for beat in state_payload["scenario"]["beats"]}


def _scene_map(state_payload: dict) -> dict[str, dict]:
    return {scene["scene_id"]: scene for scene in state_payload["scenario"]["scenes"]}


def _clue_map(state_payload: dict) -> dict[str, dict]:
    return {clue["clue_id"]: clue for clue in state_payload["scenario"]["clues"]}


def _get_session_state(
    client: TestClient,
    session_id: str,
    *,
    viewer_role: str,
    viewer_id: str | None = None,
) -> dict:
    params = {"viewer_role": viewer_role}
    if viewer_id is not None:
        params["viewer_id"] = viewer_id
    response = client.get(
        f"/sessions/{session_id}/state",
        params=params,
    )
    assert response.status_code == 200
    return response.json()


def _advance_whispering_guesthouse_to_room_truth_current(
    client: TestClient,
    session_id: str,
) -> tuple[dict, dict, dict]:
    lobby_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我在前厅调查柜台和墙角，看看有没有异常痕迹。",
            "structured_action": {"type": "investigate_lobby"},
        },
    )
    assert lobby_action.status_code == 202

    find_note_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：调查员在柜台后发现一张染潮纸条，内容把线索指向二楼走廊。",
            "structured_action": {"type": "keeper_find_note"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue_whisper_note",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "manual_find_note",
                    }
                ]
            },
        },
    )
    assert find_note_response.status_code == 202

    corridor_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我前往二楼走廊，并贴着门缝聆听门后的动静。",
            "structured_action": {"type": "listen_at_door"},
        },
    )
    assert corridor_action.status_code == 202

    corridor_manual = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：门后确实传来断续低语，调查员确认异常来自上锁客房内侧。",
            "structured_action": {"type": "keeper_confirm_whisper"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene_second_floor_corridor"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue_room_whisper",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "manual_confirm_whisper",
                    }
                ],
            },
        },
    )
    assert corridor_manual.status_code == 202

    keeper_state = _get_session_state(client, session_id, viewer_role="keeper")
    investigator_one_state = _get_session_state(
        client,
        session_id,
        viewer_role="investigator",
        viewer_id="investigator-1",
    )
    investigator_two_state = _get_session_state(
        client,
        session_id,
        viewer_role="investigator",
        viewer_id="investigator-2",
    )
    return keeper_state, investigator_one_state, investigator_two_state


def _complete_whispering_guesthouse_room_truth(
    client: TestClient,
    session_id: str,
) -> None:
    room_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我进入客房，调查桌上的日志碎页和地板上的水痕。",
            "structured_action": {"type": "investigate_room_truth"},
        },
    )
    assert room_action.status_code == 202

    room_truth_manual = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：调查员找到破碎日志并确认房内真相，潮湿低语对其理智造成了冲击。",
            "structured_action": {"type": "keeper_resolve_room_truth"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene_locked_guest_room"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue_log_fragment",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "manual_room_truth",
                    }
                ],
                "character_stat_effects": [{"actor_id": "investigator-1", "san_delta": -3}],
                "status_effects": [
                    {
                        "actor_id": "investigator-1",
                        "add_status_effects": ["受潮低语萦绕"],
                        "add_temporary_conditions": ["需要进行一次理智相关人工审阅"],
                        "add_private_notes": ["破碎日志记着住客在门后低语里逐渐失去理智。"],
                    }
                ],
            },
        },
    )
    assert room_truth_manual.status_code == 202


def test_keeper_demo_summary_stays_coherent_during_short_blackout_clinic_run(
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

    intake_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我翻看夜班登记簿，确认最后进入病历室的人，并直接推门去病历室。",
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
    action_id = intake_action.json()["authoritative_action"]["action_id"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    summary = keeper_state["keeper_workflow"]["summary"]
    active_prompt = keeper_state["keeper_workflow"]["active_prompts"][0]

    assert summary["active_prompt_count"] == 1
    assert summary["unresolved_objective_count"] == 1
    assert any("高优先提示" in line for line in summary["prompt_lines"])
    assert any("待处理场景目标" in line for line in summary["objective_lines"])
    assert any("最近完成目标" in line for line in summary["completed_objective_lines"])
    assert any("最近推进" in line for line in summary["progression_lines"])
    assert "确认停电前最后接触病历室的人是谁" in keeper_state["progress_state"]["completed_objectives"]
    assert keeper_state["progress_state"]["completed_objectives"] == (
        keeper_state["progress_state"]["completed_scene_objectives"]
    )

    prompt_update = client.post(
        f"/sessions/{session_id}/keeper-prompts/{active_prompt['prompt_id']}/status",
        json={
            "operator_id": "keeper-1",
            "status": "acknowledged",
            "add_notes": ["先让护士说完，再决定是否立刻开柜。"],
        },
    )
    assert prompt_update.status_code == 200

    updated_keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    updated_summary = updated_keeper_state["keeper_workflow"]["summary"]
    assert any("已确认" in line for line in updated_summary["prompt_lines"])
    assert any("备注：先让护士说完，再决定是否立刻开柜。" in line for line in updated_summary["prompt_lines"])
    assert any(action_id in line for line in updated_summary["progression_lines"])
    assert any("原因：" in line for line in updated_summary["summary_lines"])


def test_manual_smoke_flow_covers_room_truth_completion_and_visibility_isolation(
    client: TestClient,
) -> None:
    _register_smoke_rule_source(client, source_id="manual-smoke-whispering-guesthouse")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _minimal_whispering_guesthouse_smoke_scenario(),
            "participants": [
                make_participant("investigator-1", "里昂"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    initial_investigator = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    initial_sanity = initial_investigator["own_character_state"]["current_sanity"]

    lobby_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我在前厅调查柜台和墙角，看看有没有异常痕迹。",
            "structured_action": {"type": "investigate_lobby"},
        },
    )
    assert lobby_action.status_code == 202

    find_note_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：调查员在柜台后发现一张染潮纸条，内容把线索指向二楼走廊。",
            "structured_action": {"type": "keeper_find_note"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue_whisper_note",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "manual_find_note",
                    }
                ]
            },
        },
    )
    assert find_note_response.status_code == 202
    find_note_action = find_note_response.json()["authoritative_action"]

    keeper_after_note = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_one_after_note = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    investigator_two_after_note = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()

    beats_after_note = _beat_map(keeper_after_note)
    keeper_scenes_after_note = _scene_map(keeper_after_note)
    investigator_one_clues_after_note = _clue_map(investigator_one_after_note)
    investigator_two_clues_after_note = _clue_map(investigator_two_after_note)
    assert beats_after_note["beat_find_note"]["status"] == "completed"
    assert beats_after_note["beat_reach_corridor"]["status"] == "current"
    assert keeper_after_note["progress_state"]["current_beat"] == "beat_reach_corridor"
    assert keeper_scenes_after_note["scene_second_floor_corridor"]["revealed"] is True
    assert investigator_one_clues_after_note["clue_whisper_note"]["status"] == "shared_with_party"
    assert investigator_two_clues_after_note["clue_whisper_note"]["status"] == "shared_with_party"
    assert any(
        prompt["category"] == "scene_followup"
        and prompt["source_action_id"] == find_note_action["action_id"]
        for prompt in keeper_after_note["keeper_workflow"]["active_prompts"]
    )

    corridor_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我前往二楼走廊，并贴着门缝聆听门后的动静。",
            "structured_action": {"type": "listen_at_door"},
        },
    )
    assert corridor_action.status_code == 202

    corridor_manual = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：门后确实传来断续低语，调查员确认异常来自上锁客房内侧。",
            "structured_action": {"type": "keeper_confirm_whisper"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene_second_floor_corridor"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue_room_whisper",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "manual_confirm_whisper",
                    }
                ],
            },
        },
    )
    assert corridor_manual.status_code == 202

    keeper_after_corridor = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_one_after_corridor = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    investigator_two_after_corridor = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()

    beats_after_corridor = _beat_map(keeper_after_corridor)
    keeper_scenes_after_corridor = _scene_map(keeper_after_corridor)
    investigator_one_clues_after_corridor = _clue_map(investigator_one_after_corridor)
    investigator_two_clues_after_corridor = _clue_map(investigator_two_after_corridor)
    assert beats_after_corridor["beat_reach_corridor"]["status"] == "completed"
    assert beats_after_corridor["beat_room_truth"]["status"] == "current"
    assert keeper_after_corridor["progress_state"]["current_beat"] == "beat_room_truth"
    assert keeper_scenes_after_corridor["scene_locked_guest_room"]["revealed"] is True
    assert investigator_one_clues_after_corridor["clue_room_whisper"]["status"] == "shared_with_party"
    assert investigator_two_clues_after_corridor["clue_room_whisper"]["status"] == "shared_with_party"
    queued_prompts_after_corridor = {
        prompt["category"]: prompt for prompt in keeper_after_corridor["progress_state"]["queued_kp_prompts"]
    }
    assert queued_prompts_after_corridor["scene_followup"]["status"] == "dismissed"
    assert queued_prompts_after_corridor["scene_followup"]["dismissed_at"] is not None
    assert any(
        prompt["category"] == "sanity_review" and prompt["status"] == "pending"
        for prompt in keeper_after_corridor["keeper_workflow"]["active_prompts"]
    )
    assert queued_prompts_after_corridor["sanity_review"]["status"] == "pending"

    room_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我进入客房，调查桌上的日志碎页和地板上的水痕。",
            "structured_action": {"type": "investigate_room_truth"},
        },
    )
    assert room_action.status_code == 202

    room_truth_manual = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：调查员找到破碎日志并确认房内真相，潮湿低语对其理智造成了冲击。",
            "structured_action": {"type": "keeper_resolve_room_truth"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene_locked_guest_room"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue_log_fragment",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "manual_room_truth",
                    }
                ],
                "character_stat_effects": [{"actor_id": "investigator-1", "san_delta": -3}],
                "status_effects": [
                    {
                        "actor_id": "investigator-1",
                        "add_status_effects": ["受潮低语萦绕"],
                        "add_temporary_conditions": ["需要进行一次理智相关人工审阅"],
                        "add_private_notes": ["破碎日志记着住客在门后低语里逐渐失去理智。"],
                    }
                ],
            },
        },
    )
    assert room_truth_manual.status_code == 202

    keeper_final = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_one_final = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    investigator_two_final = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()

    final_beats = _beat_map(keeper_final)
    keeper_final_clues = _clue_map(keeper_final)
    investigator_one_final_clues = _clue_map(investigator_one_final)
    investigator_two_final_clue_ids = {clue["clue_id"] for clue in investigator_two_final["scenario"]["clues"]}
    room_truth_history = keeper_final["progress_state"]["completed_objective_history"]
    investigator_one_state = investigator_one_final["own_character_state"]
    investigator_two_state = investigator_two_final["own_character_state"]
    keeper_investigator_one_state = keeper_final["visible_character_states_by_actor"]["investigator-1"]

    assert final_beats["beat_room_truth"]["status"] == "completed"
    assert any(record["objective_id"] == "obj_room_truth" for record in room_truth_history)
    assert all(
        objective["objective_id"] != "obj_room_truth"
        for objective in keeper_final["keeper_workflow"]["unresolved_objectives"]
    )
    assert investigator_one_final_clues["clue_log_fragment"]["status"] == "private_to_actor"
    assert "clue_log_fragment" not in investigator_two_final_clue_ids
    assert keeper_final_clues["clue_log_fragment"]["status"] == "private_to_actor"
    assert investigator_one_state["current_sanity"] == initial_sanity - 3
    assert "受潮低语萦绕" in investigator_one_state["status_effects"]
    assert "需要进行一次理智相关人工审阅" in investigator_one_state["temporary_conditions"]
    assert any("破碎日志记着住客在门后低语里逐渐失去理智。" in note for note in investigator_one_state["private_notes"])
    assert any(
        "破碎日志记着住客在门后低语里逐渐失去理智。" in note
        for note in keeper_investigator_one_state["private_notes"]
    )
    final_prompt_states = {
        prompt["category"]: prompt for prompt in keeper_final["progress_state"]["queued_kp_prompts"]
    }
    assert final_prompt_states["sanity_review"]["status"] == "dismissed"
    assert final_prompt_states["sanity_review"]["dismissed_at"] is not None
    assert {"clue_whisper_note", "clue_room_whisper", "clue_log_fragment"} == set(keeper_final_clues)
    assert {"clue_whisper_note", "clue_room_whisper", "clue_log_fragment"} == set(
        investigator_one_final_clues
    )
    assert investigator_two_final_clue_ids == {"clue_whisper_note", "clue_room_whisper"}
    assert "clue_log_fragment" in keeper_investigator_one_state["clue_ids"]
    assert "clue_log_fragment" not in investigator_two_state["clue_ids"]


def test_snapshot_import_allows_completing_room_truth_third_beat_without_losing_history(
    client: TestClient,
) -> None:
    _register_smoke_rule_source(client, source_id="manual-smoke-room-truth-import")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": _minimal_whispering_guesthouse_smoke_scenario(),
            "participants": [
                make_participant("investigator-1", "里昂"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    original_session_id = start_response.json()["session_id"]

    keeper_before_snapshot, investigator_one_before_snapshot, investigator_two_before_snapshot = (
        _advance_whispering_guesthouse_to_room_truth_current(client, original_session_id)
    )
    beats_before_snapshot = _beat_map(keeper_before_snapshot)
    investigator_one_clues_before_snapshot = _clue_map(investigator_one_before_snapshot)
    investigator_two_clue_ids_before_snapshot = {
        clue["clue_id"] for clue in investigator_two_before_snapshot["scenario"]["clues"]
    }

    assert beats_before_snapshot["beat_find_note"]["status"] == "completed"
    assert beats_before_snapshot["beat_reach_corridor"]["status"] == "completed"
    assert beats_before_snapshot["beat_room_truth"]["status"] == "current"
    assert keeper_before_snapshot["progress_state"]["current_beat"] == "beat_room_truth"
    assert investigator_one_clues_before_snapshot["clue_room_whisper"]["status"] == "shared_with_party"
    assert "clue_log_fragment" not in investigator_two_clue_ids_before_snapshot
    assert any(
        prompt["category"] == "sanity_review" and prompt["status"] == "pending"
        for prompt in keeper_before_snapshot["keeper_workflow"]["active_prompts"]
    )

    original_snapshot_response = client.get(f"/sessions/{original_session_id}/snapshot")
    assert original_snapshot_response.status_code == 200
    original_snapshot = original_snapshot_response.json()
    original_timeline_len = len(original_snapshot["timeline"])

    import_response = client.post("/sessions/import", json=original_snapshot)
    assert import_response.status_code == 201
    imported_session_id = import_response.json()["new_session_id"]

    imported_keeper_before_finish = _get_session_state(
        client,
        imported_session_id,
        viewer_role="keeper",
    )
    imported_investigator_one_before_finish = _get_session_state(
        client,
        imported_session_id,
        viewer_role="investigator",
        viewer_id="investigator-1",
    )
    imported_investigator_two_before_finish = _get_session_state(
        client,
        imported_session_id,
        viewer_role="investigator",
        viewer_id="investigator-2",
    )
    imported_snapshot_before_finish_response = client.get(
        f"/sessions/{imported_session_id}/snapshot"
    )
    assert imported_snapshot_before_finish_response.status_code == 200
    imported_snapshot_before_finish = imported_snapshot_before_finish_response.json()

    imported_beats_before_finish = _beat_map(imported_keeper_before_finish)
    imported_prompt_before_finish = next(
        prompt
        for prompt in imported_keeper_before_finish["keeper_workflow"]["active_prompts"]
        if prompt["category"] == "sanity_review"
    )
    pre_finish_sanity = imported_investigator_one_before_finish["own_character_state"]["current_sanity"]
    pre_finish_visible_event_count = len(imported_keeper_before_finish["visible_events"])
    pre_finish_authoritative_action_count = len(
        imported_keeper_before_finish["visible_authoritative_actions"]
    )

    assert imported_beats_before_finish["beat_room_truth"]["status"] == "current"
    assert imported_keeper_before_finish["progress_state"]["current_beat"] == "beat_room_truth"
    assert imported_prompt_before_finish["status"] == "pending"
    assert "clue_log_fragment" not in {
        clue["clue_id"] for clue in imported_investigator_two_before_finish["scenario"]["clues"]
    }
    assert len(imported_snapshot_before_finish["timeline"]) == original_timeline_len + 1
    assert imported_snapshot_before_finish["timeline"][-1]["event_type"] == "import"

    _complete_whispering_guesthouse_room_truth(client, imported_session_id)

    keeper_final = _get_session_state(client, imported_session_id, viewer_role="keeper")
    investigator_one_final = _get_session_state(
        client,
        imported_session_id,
        viewer_role="investigator",
        viewer_id="investigator-1",
    )
    investigator_two_final = _get_session_state(
        client,
        imported_session_id,
        viewer_role="investigator",
        viewer_id="investigator-2",
    )
    imported_snapshot_after_finish_response = client.get(
        f"/sessions/{imported_session_id}/snapshot"
    )
    assert imported_snapshot_after_finish_response.status_code == 200
    imported_snapshot_after_finish = imported_snapshot_after_finish_response.json()

    final_beats = _beat_map(keeper_final)
    keeper_final_clues = _clue_map(keeper_final)
    investigator_one_final_clues = _clue_map(investigator_one_final)
    investigator_two_final_clue_ids = {
        clue["clue_id"] for clue in investigator_two_final["scenario"]["clues"]
    }
    room_truth_history = keeper_final["progress_state"]["completed_objective_history"]
    investigator_one_state = investigator_one_final["own_character_state"]
    investigator_two_state = investigator_two_final["own_character_state"]
    keeper_investigator_one_state = keeper_final["visible_character_states_by_actor"]["investigator-1"]

    assert final_beats["beat_room_truth"]["status"] == "completed"
    assert any(record["objective_id"] == "obj_room_truth" for record in room_truth_history)
    assert all(
        objective["objective_id"] != "obj_room_truth"
        for objective in keeper_final["keeper_workflow"]["unresolved_objectives"]
    )
    assert investigator_one_final_clues["clue_log_fragment"]["status"] == "private_to_actor"
    assert "clue_log_fragment" not in investigator_two_final_clue_ids
    assert keeper_final_clues["clue_log_fragment"]["status"] == "private_to_actor"
    assert investigator_one_state["current_sanity"] == pre_finish_sanity - 3
    assert "受潮低语萦绕" in investigator_one_state["status_effects"]
    assert "需要进行一次理智相关人工审阅" in investigator_one_state["temporary_conditions"]
    assert any("破碎日志记着住客在门后低语里逐渐失去理智。" in note for note in investigator_one_state["private_notes"])
    assert any(
        "破碎日志记着住客在门后低语里逐渐失去理智。" in note
        for note in keeper_investigator_one_state["private_notes"]
    )
    final_prompt_states = {
        prompt["category"]: prompt for prompt in keeper_final["progress_state"]["queued_kp_prompts"]
    }
    assert imported_prompt_before_finish["status"] == "pending"
    assert final_prompt_states["sanity_review"]["status"] == "dismissed"
    assert final_prompt_states["sanity_review"]["dismissed_at"] is not None
    assert len(keeper_final["visible_events"]) == pre_finish_visible_event_count + 2
    assert len(keeper_final["visible_authoritative_actions"]) == (
        pre_finish_authoritative_action_count + 2
    )
    assert any(
        event["text"] == "KP裁定：门后确实传来断续低语，调查员确认异常来自上锁客房内侧。"
        for event in keeper_final["visible_events"]
    )
    assert any(
        event["text"] == "KP裁定：调查员找到破碎日志并确认房内真相，潮湿低语对其理智造成了冲击。"
        for event in keeper_final["visible_events"]
    )
    assert len(imported_snapshot_after_finish["timeline"]) == (
        len(imported_snapshot_before_finish["timeline"]) + 2
    )
    assert imported_snapshot_after_finish["timeline"][-2]["text"] == (
        "我进入客房，调查桌上的日志碎页和地板上的水痕。"
    )
    assert imported_snapshot_after_finish["timeline"][-1]["text"] == (
        "KP裁定：调查员找到破碎日志并确认房内真相，潮湿低语对其理智造成了冲击。"
    )
    assert imported_snapshot_after_finish["authoritative_actions"][-2]["text"] == (
        "我进入客房，调查桌上的日志碎页和地板上的水痕。"
    )
    assert imported_snapshot_after_finish["authoritative_actions"][-1]["text"] == (
        "KP裁定：调查员找到破碎日志并确认房内真相，潮湿低语对其理智造成了冲击。"
    )
    assert imported_investigator_one_before_finish["keeper_workflow"] is None
    assert investigator_two_final["keeper_workflow"] is None
    assert "clue_log_fragment" in keeper_investigator_one_state["clue_ids"]
    assert "clue_log_fragment" not in investigator_two_state["clue_ids"]


def test_session_export_returns_keeper_view_json_for_backup_and_review(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                start_scene_id="scene.study",
                clues=[
                    {
                        "clue_id": "clue-ledger",
                        "title": "账页残片",
                        "text": "残片记录着一个可疑房间号。",
                        "visibility_scope": "kp_only",
                    }
                ],
                scenes=[
                    {
                        "scene_id": "scene.study",
                        "title": "书房",
                        "summary": "书桌上堆着账册和湿掉的纸张。",
                        "revealed": True,
                        "linked_clue_ids": ["clue-ledger"],
                        "scene_objectives": [
                            {
                                "objective_id": "obj.study.review_ledger",
                                "text": "确认账页是否能推动下一步调查",
                                "beat_id": "beat.review_ledger",
                            }
                        ],
                        "keeper_notes": ["这是 keeper 导出里应保留的场景备注。"],
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat.review_ledger",
                        "title": "查看账页",
                        "start_unlocked": True,
                        "complete_conditions": {
                            "clue_discovered": {"clue_id": "clue-ledger"}
                        },
                        "consequences": [
                            {
                                "queue_kp_prompts": [
                                    {
                                        "prompt_text": "KP：确认是否要根据账页推进下一拍。",
                                        "category": "scene_followup",
                                        "reason": "账页已经把调查指向可疑房间。",
                                    }
                                ],
                                "mark_scene_objectives_complete": [
                                    {"objective_id": "obj.study.review_ledger"}
                                ],
                            }
                        ],
                    }
                ],
            ),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我翻出湿掉的账页残片查看上面的房间号。",
            "structured_action": {"type": "review_ledger"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-ledger",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "review_ledger",
                    }
                ]
            },
        },
    )
    assert action_response.status_code == 202

    manual_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": KEEPER_ACTOR_ID,
            "actor_id": KEEPER_ACTOR_ID,
            "actor_type": "keeper",
            "action_text": "KP裁定：记录调查员已经确认这条账页值得继续跟进。",
            "structured_action": {"type": "keeper_followup_note"},
            "effects": {
                "status_effects": [
                    {
                        "actor_id": "investigator-1",
                        "add_private_notes": ["keeper 手动裁定：账页线索可继续追查。"],
                    }
                ]
            },
        },
    )
    assert manual_response.status_code == 202

    export_response = client.get(f"/sessions/{session_id}/export")
    assert export_response.status_code == 200
    export_payload = export_response.json()

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert keeper_state.status_code == 200
    keeper_payload = keeper_state.json()

    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    )
    assert investigator_state.status_code == 200
    investigator_payload = investigator_state.json()

    assert export_payload == keeper_payload
    assert export_payload["session_id"] == session_id
    assert export_payload["scenario"]["beats"]
    assert export_payload["participants"][0]["actor_id"] == "investigator-1"
    assert len(export_payload["visible_authoritative_actions"]) == 2
    assert export_payload["progress_state"]["completed_beats"] == ["beat.review_ledger"]
    assert export_payload["keeper_workflow"]["active_prompts"][0]["category"] == "scene_followup"
    assert export_payload["scenario"]["scenes"][0]["keeper_notes"] == ["这是 keeper 导出里应保留的场景备注。"]
    assert investigator_payload["scenario"]["beats"] == []
    assert investigator_payload["keeper_workflow"] is None
    assert investigator_payload["scenario"]["scenes"][0]["keeper_notes"] == []
