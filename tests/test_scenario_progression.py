from __future__ import annotations

from fastapi.testclient import TestClient

from coc_runner.domain.models import ScenarioProgressState
from tests.helpers import make_participant, make_scenario


def _register_spot_hidden_rule(client: TestClient, *, source_id: str) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": "rulebook",
            "source_format": "markdown",
            "source_title_zh": "侦查规则",
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
            "content": "# 侦查\n侦查用于发现隐藏线索与可疑痕迹。",
        },
    )
    assert ingest_response.status_code == 200


def _beat_map(state_payload: dict) -> dict[str, dict]:
    return {beat["beat_id"]: beat for beat in state_payload["scenario"]["beats"]}


def _trigger_keeper_prompt_assignment(
    client: TestClient,
    *,
    keeper_id: str,
    assigned_to: str | None = None,
) -> dict:
    prompt_payload = {
        "prompt_text": "KP：确认是否需要跟进这条新线索。",
        "category": "assignment_check",
        "reason": "测试 keeper prompt 的分配目标回填。",
    }
    if assigned_to is not None:
        prompt_payload["assigned_to"] = assigned_to

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": keeper_id,
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue-ledger",
                        "title": "关键账页",
                        "text": "账页上记着可推动调查的关键日期。",
                        "visibility_scope": "kp_only",
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat-log-review",
                        "title": "查看账页",
                        "start_unlocked": True,
                        "complete_conditions": {
                            "clue_discovered": {"clue_id": "clue-ledger"}
                        },
                        "consequences": [{"queue_kp_prompts": [prompt_payload]}],
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
            "action_text": "我从账册里翻出关键账页。",
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

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    prompts = keeper_state["keeper_workflow"]["active_prompts"]
    assert len(prompts) == 1
    return prompts[0]


def test_approved_action_unlocks_followup_beat(client: TestClient) -> None:
    _register_spot_hidden_rule(client, source_id="beat-approved-rule")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                beats=[
                    {
                        "beat_id": "beat-search-room",
                        "title": "搜索房间",
                        "start_unlocked": True,
                        "scene_objective": "先确认房间内是否有异常。",
                    },
                    {
                        "beat_id": "beat-followup-search",
                        "title": "深入检查痕迹",
                        "unlock_conditions": {
                            "deterministic_handoff_topic_matches": {"topic": "term:spot_hidden"}
                        },
                        "scene_objective": "在有规则支撑时推进更深入的搜查。",
                    },
                ]
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议按侦查规则先确认墙面的细微痕迹。",
            "structured_action": {"type": "investigate_search"},
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert draft_response.status_code == 202
    draft_id = draft_response.json()["draft_action"]["draft_id"]

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 200
    authoritative_action = review_response.json()["authoritative_action"]
    transition_types = {
        transition["beat_id"]: transition["transition"]
        for transition in authoritative_action["applied_beat_transitions"]
    }
    assert transition_types["beat-followup-search"] == "unlocked"

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    beats = _beat_map(keeper_state)
    assert beats["beat-search-room"]["status"] == "current"
    assert beats["beat-followup-search"]["status"] == "unlocked"
    assert "beat-followup-search" in keeper_state["progress_state"]["unlocked_beats"]


def test_beat_completion_after_clue_discovery_advances_next_beat(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue-clipping",
                        "title": "桌上剪报",
                        "text": "剪报上记录着旅店旧案的日期。",
                        "visibility_scope": "kp_only",
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat-search-desk",
                        "title": "搜索书桌",
                        "start_unlocked": True,
                        "required_clues": ["clue-clipping"],
                        "complete_conditions": {
                            "clue_discovered": {"clue_id": "clue-clipping"}
                        },
                        "next_beats": ["beat-read-clipping"],
                    },
                    {
                        "beat_id": "beat-read-clipping",
                        "title": "研读剪报",
                        "scene_objective": "根据剪报信息决定下一步。",
                    },
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
            "action_text": "我翻开书桌抽屉并找到一张旧剪报。",
            "structured_action": {"type": "inspect_desk"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-clipping",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "inspect_desk",
                    }
                ]
            },
        },
    )
    assert action_response.status_code == 202
    transitions = action_response.json()["authoritative_action"]["applied_beat_transitions"]
    assert any(
        transition["beat_id"] == "beat-search-desk" and transition["transition"] == "completed"
        for transition in transitions
    )
    assert any(
        transition["beat_id"] == "beat-read-clipping" and transition["transition"] == "unlocked"
        for transition in transitions
    )

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    beats = _beat_map(keeper_state)
    assert beats["beat-search-desk"]["status"] == "completed"
    assert beats["beat-read-clipping"]["status"] == "current"
    assert keeper_state["progress_state"]["current_beat"] == "beat-read-clipping"
    assert "beat-search-desk" in keeper_state["progress_state"]["completed_beats"]


def test_fail_forward_activation_unlocks_core_clue_beat_without_single_point_failure(
    client: TestClient,
) -> None:
    _register_spot_hidden_rule(client, source_id="beat-fail-forward-rule")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue-hidden-door",
                        "title": "暗门划痕",
                        "text": "墙面磨损说明这里原本有暗门。",
                        "visibility_scope": "kp_only",
                        "core_clue_flag": True,
                        "alternate_paths": ["调查旧图纸", "询问木匠"],
                        "fail_forward_text": "即使侦查失败，也会因墙面异常磨损意识到这里不对劲。",
                        "fail_forward_triggers": [
                            {
                                "action_types": ["investigate_search"],
                                "required_topic": "term:spot_hidden",
                                "fallback_status": "partially_understood",
                                "reveal_to": "actor",
                                "assign_to_actor": True,
                                "discovered_via": "search_fail_forward",
                            }
                        ],
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat-search-study",
                        "title": "搜索书房",
                        "start_unlocked": True,
                    },
                    {
                        "beat_id": "beat-enter-hidden-room",
                        "title": "进入暗门后空间",
                        "required_clues": ["clue-hidden-door"],
                        "unlock_conditions": {
                            "deterministic_handoff_topic_matches": {"topic": "term:spot_hidden"}
                        },
                    },
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
            "action_text": "我沿着墙面和书架反复搜索异常磨损。",
            "structured_action": {"type": "investigate_search"},
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert action_response.status_code == 202
    transitions = action_response.json()["authoritative_action"]["applied_beat_transitions"]
    assert any(
        transition["beat_id"] == "beat-enter-hidden-room"
        and transition["transition"] == "fail_forward_activated"
        for transition in transitions
    )
    assert any(
        transition["beat_id"] == "beat-enter-hidden-room"
        and transition["transition"] == "unlocked"
        for transition in transitions
    )

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    beats = _beat_map(keeper_state)
    assert beats["beat-enter-hidden-room"]["status"] == "unlocked"
    assert keeper_state["progress_state"]["blocked_beats"] == []
    assert "clue-hidden-door" in keeper_state["progress_state"]["activated_fail_forward_clues"]
    assert "暗门划痕" in keeper_state["progress_state"]["activated_fail_forward_clues"]
    assert any(
        transition["transition"] == "fail_forward_activated"
        and transition["reason"] == "核心线索触发失手前进，避免单点卡死"
        for transition in keeper_state["progress_state"]["transition_history"]
    )


def test_beat_completion_applies_consequences_and_records_progress_audit(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue-ledger",
                        "title": "账本残页",
                        "text": "残页记着地下储物间的出入记录。",
                        "visibility_scope": "kp_only",
                    },
                    {
                        "clue_id": "clue-hidden-locker",
                        "title": "暗格钥匙孔",
                        "text": "书架后的暗格锁孔需要特殊钥匙。",
                        "visibility_scope": "kp_only",
                    },
                ],
                scenes=[
                    {
                        "scene_id": "scene.inn_office",
                        "title": "旅店办公室",
                        "summary": "办公室桌面铺满了账册和旅客登记簿。",
                        "revealed": False,
                    },
                    {
                        "scene_id": "scene.hidden_locker",
                        "title": "办公室暗格",
                        "summary": "书架后方藏着一处狭小暗格。",
                        "revealed": False,
                        "linked_clue_ids": ["clue-hidden-locker"],
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat-office-search",
                        "title": "搜索办公室",
                        "start_unlocked": True,
                        "scene_objective": "确认办公室里是否有能推进剧情的证据。",
                        "required_clues": ["clue-ledger"],
                        "complete_conditions": {
                            "clue_discovered": {"clue_id": "clue-ledger"}
                        },
                        "consequences": [
                            {
                                "reveal_clues": [
                                    {
                                        "clue_id": "clue-hidden-locker",
                                        "share_with_party": False,
                                        "visible_to_actor_ids": ["investigator-1"],
                                        "owner_actor_ids": ["investigator-1"],
                                        "discovered_by_actor_ids": ["investigator-1"],
                                        "discovered_via": "beat:beat-office-search",
                                    }
                                ],
                                "reveal_scenes": [
                                    {
                                        "scene_ref": "scene.hidden_locker",
                                        "summary": "办公室书架后存在暗格。",
                                    }
                                ],
                                "apply_statuses": [
                                    {
                                        "actor_id": "investigator-1",
                                        "add_status_effects": ["受惊"],
                                    }
                                ],
                                "npc_attitude_updates": [
                                    {
                                        "npc_id": "npc-innkeeper",
                                        "attitude": "defensive",
                                        "note": "老板开始刻意回避调查员。",
                                    }
                                ],
                                "grant_private_notes": [
                                    {
                                        "actor_id": "investigator-1",
                                        "note": "账本残页把储物间和失踪者联系到了一起。",
                                    }
                                ],
                                "queue_kp_prompts": [
                                    {
                                        "prompt_text": "KP：让旅店老板在提到储物间时明显紧张。",
                                        "category": "npc_reaction",
                                    }
                                ],
                                "mark_scene_objectives_complete": [{}],
                            }
                        ],
                    },
                    {
                        "beat_id": "beat-innkeeper-reaction",
                        "title": "处理老板反应",
                        "unlock_conditions": {
                            "all_of": [
                                {
                                    "beat_status_is": {
                                        "beat_id": "beat-office-search",
                                        "status": "completed",
                                    }
                                },
                                {"current_scene_in": {"scene_ids": ["scene.inn_office"]}},
                                {"any_actor_has_status": {"status": "受惊"}},
                            ]
                        },
                    },
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
            "action_text": "我检查办公室抽屉里的账本残页。",
            "structured_action": {"type": "inspect_office"},
            "effects": {
                "scene_transitions": [
                    {
                        "scene_id": "scene.inn_office",
                    }
                ],
                "clue_state_effects": [
                    {
                        "clue_id": "clue-ledger",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "inspect_office",
                    }
                ],
            },
        },
    )
    assert action_response.status_code == 202
    authoritative_action = action_response.json()["authoritative_action"]

    transitions = authoritative_action["applied_beat_transitions"]
    office_completion = next(
        transition for transition in transitions if transition["beat_id"] == "beat-office-search"
    )
    reaction_unlock = next(
        transition
        for transition in transitions
        if transition["beat_id"] == "beat-innkeeper-reaction"
        and transition["transition"] == "unlocked"
    )
    assert office_completion["transition"] == "completed"
    assert office_completion["trigger_action_id"] == authoritative_action["action_id"]
    assert "clue_discovered:clue-ledger" in office_completion["condition_refs"]
    assert "reveal_scene:scene.hidden_locker" in office_completion["consequence_refs"]
    assert "queue_kp_prompt:npc_reaction" in office_completion["consequence_refs"]
    assert reaction_unlock["reason"] == "满足解锁条件"
    assert "beat_status_is:beat-office-search:completed" in reaction_unlock["condition_refs"]
    assert "current_scene_in_scene_ids:scene.inn_office" in reaction_unlock["condition_refs"]
    assert "any_actor_has_status:*:受惊" in reaction_unlock["condition_refs"]

    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()

    investigator_clues = {clue["title"]: clue for clue in investigator_state["scenario"]["clues"]}
    assert investigator_clues["暗格钥匙孔"]["status"] == "private_to_actor"
    assert "受惊" in investigator_state["own_character_state"]["status_effects"]
    assert any(
        "账本残页把储物间和失踪者联系到了一起。" in note
        for note in investigator_state["own_character_state"]["private_notes"]
    )

    keeper_progress = keeper_state["progress_state"]
    beats = _beat_map(keeper_state)
    assert beats["beat-office-search"]["status"] == "completed"
    assert beats["beat-innkeeper-reaction"]["status"] == "current"
    assert set(keeper_progress["revealed_scene_refs"]) == {"scene.inn_office", "scene.hidden_locker"}
    assert "确认办公室里是否有能推进剧情的证据。" in keeper_progress["completed_scene_objectives"]
    assert keeper_progress["npc_attitudes"]["npc-innkeeper"] == "defensive"
    assert keeper_progress["queued_kp_prompts"][0]["category"] == "npc_reaction"
    assert keeper_progress["queued_kp_prompts"][0]["source_action_id"] == authoritative_action["action_id"]
    assert keeper_progress["transition_history"][-1]["trigger_action_id"] == authoritative_action["action_id"]


def test_keeper_prompt_defaults_assigned_to_current_session_keeper(client: TestClient) -> None:
    prompt = _trigger_keeper_prompt_assignment(
        client,
        keeper_id="keeper_custom_001",
    )

    assert prompt["assigned_to"] == "keeper_custom_001"


def test_keeper_prompt_preserves_explicit_assigned_to_over_session_keeper(client: TestClient) -> None:
    prompt = _trigger_keeper_prompt_assignment(
        client,
        keeper_id="keeper_custom_001",
        assigned_to="keeper-ops",
    )

    assert prompt["assigned_to"] == "keeper-ops"


def test_actor_scoped_clue_visibility_conditions_unlock_followup_beat(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue-private-ledger",
                        "title": "私密账页",
                        "text": "账页只暴露给找到它的调查员。",
                        "visibility_scope": "kp_only",
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat-find-ledger",
                        "title": "找到账页",
                        "start_unlocked": True,
                    },
                    {
                        "beat_id": "beat-private-inference",
                        "title": "私下推断账页意义",
                        "unlock_conditions": {
                            "all_of": [
                                {
                                    "clue_visible_to_actor": {
                                        "actor_id": "investigator-1",
                                        "clue_id": "clue-private-ledger",
                                    }
                                },
                                {
                                    "actor_owns_clue": {
                                        "actor_id": "investigator-1",
                                        "clue_id": "clue-private-ledger",
                                    }
                                },
                            ]
                        },
                    },
                ],
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我把账页悄悄收起来自己先看。",
            "structured_action": {"type": "take_private_note"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-private-ledger",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "take_private_note",
                    }
                ]
            },
        },
    )
    assert action_response.status_code == 202
    transitions = action_response.json()["authoritative_action"]["applied_beat_transitions"]
    assert any(
        transition["beat_id"] == "beat-private-inference"
        and transition["transition"] == "unlocked"
        for transition in transitions
    )

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    beats = _beat_map(keeper_state)
    assert beats["beat-find-ledger"]["status"] == "current"
    assert beats["beat-private-inference"]["status"] == "unlocked"
    assert any(
        "clue_visible_to_actor:investigator-1:clue-private-ledger" in transition["condition_refs"]
        for transition in keeper_state["progress_state"]["transition_history"]
        if transition["beat_id"] == "beat-private-inference"
    )


def test_non_authoritative_draft_does_not_advance_scenario_progress(client: TestClient) -> None:
    _register_spot_hidden_rule(client, source_id="beat-draft-rule")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                beats=[
                    {
                        "beat_id": "beat-opening",
                        "title": "开场调查",
                        "start_unlocked": True,
                    },
                    {
                        "beat_id": "beat-hidden-trail",
                        "title": "发现隐藏痕迹",
                        "unlock_conditions": {
                            "deterministic_handoff_topic_matches": {"topic": "term:spot_hidden"}
                        },
                    },
                ]
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议先按侦查规则去检查地板边缘。",
            "structured_action": {"type": "investigate_search"},
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert draft_response.status_code == 202
    assert draft_response.json()["draft_action"]["review_status"] == "pending"

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    beats = _beat_map(keeper_state)
    assert beats["beat-opening"]["status"] == "current"
    assert beats["beat-hidden-trail"]["status"] == "locked"
    assert keeper_state["progress_state"]["unlocked_beats"] == ["beat-opening"]
    assert keeper_state["progress_state"]["activated_fail_forward_clues"] == []
    assert keeper_state["progress_state"]["transition_history"] == []


def test_keeper_prompt_lifecycle_supports_acknowledge_complete_and_dismiss(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue-ledger",
                        "title": "账页线索",
                        "text": "账页指出了新的调查方向。",
                        "visibility_scope": "kp_only",
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat-log-review",
                        "title": "查看账页",
                        "start_unlocked": True,
                        "complete_conditions": {
                            "clue_discovered": {"clue_id": "clue-ledger"}
                        },
                        "consequences": [
                            {
                                "queue_kp_prompts": [
                                    {
                                        "prompt_text": "KP：让证人表现得明显紧张。",
                                        "category": "npc_reaction",
                                        "priority": "high",
                                        "assigned_to": "keeper-1",
                                        "reason": "证人被问到关键日期时开始躲闪。",
                                    },
                                    {
                                        "prompt_text": "KP：确认是否需要额外风险裁定。",
                                        "category": "risk_review",
                                        "reason": "调查员开始接近危险区域。",
                                    },
                                ]
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
            "action_text": "我从账册里翻出关键日期。",
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

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    prompts = keeper_state["keeper_workflow"]["active_prompts"]
    assert len(prompts) == 2
    prompt_ids_by_category = {prompt["category"]: prompt["prompt_id"] for prompt in prompts}
    assert all(prompt["status"] == "pending" for prompt in prompts)
    npc_prompt = next(prompt for prompt in prompts if prompt["category"] == "npc_reaction")
    assert npc_prompt["priority"] == "high"
    assert npc_prompt["assigned_to"] == "keeper-1"
    assert npc_prompt["notes"] == []

    acknowledge_response = client.post(
        f"/sessions/{session_id}/keeper-prompts/{prompt_ids_by_category['npc_reaction']}/status",
        json={
            "operator_id": "keeper-1",
            "status": "acknowledged",
            "priority": "medium",
            "assigned_to": "keeper-ops",
            "add_notes": ["先观察证人反应，再决定是否追问。"],
        },
    )
    assert acknowledge_response.status_code == 200
    acknowledged_prompt = acknowledge_response.json()["prompt"]
    assert acknowledged_prompt["status"] == "acknowledged"
    assert acknowledged_prompt["acknowledged_at"] is not None
    assert acknowledged_prompt["priority"] == "medium"
    assert acknowledged_prompt["assigned_to"] == "keeper-ops"
    assert acknowledged_prompt["notes"] == ["先观察证人反应，再决定是否追问。"]
    acknowledged_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert any(
        prompt["prompt_id"] == prompt_ids_by_category["npc_reaction"]
        and prompt["status"] == "acknowledged"
        and prompt["assigned_to"] == "keeper-ops"
        for prompt in acknowledged_state["keeper_workflow"]["active_prompts"]
    )
    acknowledged_summary = acknowledged_state["keeper_workflow"]["summary"]
    assert acknowledged_summary["active_prompt_count"] == 2
    assert acknowledged_summary["unresolved_objective_count"] == 0
    assert any("指派：keeper-ops" in line for line in acknowledged_summary["summary_lines"])

    completed_response = client.post(
        f"/sessions/{session_id}/keeper-prompts/{prompt_ids_by_category['npc_reaction']}/status",
        json={"operator_id": "keeper-1", "status": "completed"},
    )
    assert completed_response.status_code == 200
    assert completed_response.json()["prompt"]["completed_at"] is not None

    dismissed_response = client.post(
        f"/sessions/{session_id}/keeper-prompts/{prompt_ids_by_category['risk_review']}/status",
        json={"operator_id": "keeper-1", "status": "dismissed"},
    )
    assert dismissed_response.status_code == 200
    assert dismissed_response.json()["prompt"]["dismissed_at"] is not None

    final_keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert final_keeper_state["keeper_workflow"]["active_prompts"] == []
    final_prompts = {
        prompt["category"]: prompt for prompt in final_keeper_state["progress_state"]["queued_kp_prompts"]
    }
    assert final_prompts["npc_reaction"]["status"] == "completed"
    assert final_prompts["risk_review"]["status"] == "dismissed"
    final_summary = final_keeper_state["keeper_workflow"]["summary"]
    assert final_summary["active_prompt_count"] == 0
    assert any(
        transition["beat_id"] == "beat-log-review"
        for transition in final_summary["recent_beat_transitions"]
    )
    assert any("最近推进" in line for line in final_summary["summary_lines"])


def test_scene_objective_takes_precedence_over_duplicate_beat_fallback_objective(
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
                        "clue_id": "clue-diary",
                        "title": "日记残页",
                        "text": "残页里提到一处关键会面。",
                        "visibility_scope": "kp_only",
                    }
                ],
                scenes=[
                    {
                        "scene_id": "scene.study",
                        "title": "书房",
                        "summary": "书桌上堆着文件和灰尘。",
                        "revealed": True,
                        "scene_objectives": [
                            {
                                "objective_id": "objective.study.inspect_desk",
                                "text": "先确认书桌里是否有能推进剧情的记录",
                                "beat_id": "beat.inspect_desk",
                            }
                        ],
                    }
                ],
                beats=[
                    {
                        "beat_id": "beat.inspect_desk",
                        "title": "检查书桌",
                        "start_unlocked": True,
                        "scene_objective": "这条 beat 说明不应生成重复目标",
                        "required_clues": ["clue-diary"],
                        "complete_conditions": {
                            "clue_discovered": {"clue_id": "clue-diary"}
                        },
                        "consequences": [{"mark_scene_objectives_complete": [{}]}],
                    }
                ],
            ),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    initial_keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    unresolved = initial_keeper_state["keeper_workflow"]["unresolved_objectives"]
    assert [objective["objective_id"] for objective in unresolved] == ["objective.study.inspect_desk"]
    assert unresolved[0]["origin"] == "scene"

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我打开抽屉找到了日记残页。",
            "structured_action": {"type": "inspect_desk"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-diary",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "inspect_desk",
                    }
                ]
            },
        },
    )
    assert action_response.status_code == 202

    final_keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert final_keeper_state["keeper_workflow"]["unresolved_objectives"] == []
    assert "先确认书桌里是否有能推进剧情的记录" in final_keeper_state["progress_state"]["completed_scene_objectives"]
    assert final_keeper_state["progress_state"]["completed_objectives"] == (
        final_keeper_state["progress_state"]["completed_scene_objectives"]
    )
    assert final_keeper_state["keeper_workflow"]["summary"]["recently_completed_objectives"][0]["text"] == (
        "先确认书桌里是否有能推进剧情的记录"
    )
    assert all(
        objective["objective_id"] != "beat:beat.inspect_desk"
        for objective in final_keeper_state["progress_state"]["active_scene_objectives"]
    )


def test_completed_objective_legacy_and_canonical_names_stay_mirrored() -> None:
    progress_state = ScenarioProgressState.model_validate(
        {"completed_scene_objectives": ["旧目标"], "completed_objectives": ["新目标"]}
    )
    assert progress_state.completed_objectives == ["新目标", "旧目标"]
    assert progress_state.completed_scene_objectives == ["新目标", "旧目标"]
