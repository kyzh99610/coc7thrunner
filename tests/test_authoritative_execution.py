from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import make_participant, make_scenario


def _register_spot_hidden_rule(client: TestClient, *, source_id: str = "authoritative-rule") -> None:
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


def test_human_authoritative_action_executes_structured_effects(
    client: TestClient,
) -> None:
    _register_spot_hidden_rule(client, source_id="human-authoritative-rule")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "地板灰痕",
                        "text": "地板上的灰痕指向旅店走廊深处。",
                        "visibility_scope": "kp_only",
                    }
                ]
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
            "action_text": "我检查地板灰痕并收起一枚破损徽章。",
            "structured_action": {
                "type": "investigate_floor",
                "required_handoff_topic": "term:spot_hidden",
            },
            "effects": {
                "scene_transitions": [
                    {
                        "title": "旅店走廊",
                        "summary": "调查员沿着灰痕推进到旅店走廊。",
                        "phase": "investigation",
                        "required_current_phase": "setup",
                        "consequence_tags": ["警觉提升"],
                    }
                ],
                "clue_state_effects": [
                    {
                        "clue_title": "地板灰痕",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "discovered_via": "investigate_floor",
                    }
                ],
                "character_stat_effects": [{"actor_id": "investigator-1", "san_delta": -1}],
                "inventory_effects": [
                    {"actor_id": "investigator-1", "add_items": ["破损徽章"]}
                ],
                "status_effects": [
                    {"actor_id": "investigator-1", "add_status_effects": ["紧张"]}
                ],
            },
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert action_response.status_code == 202
    action_payload = action_response.json()

    assert action_payload["authoritative_action"]["source_type"] == "human_player"
    assert action_payload["authoritative_action"]["effects"]["scene_transitions"]
    assert action_payload["authoritative_action"]["applied_effects"]
    assert action_payload["authoritative_event"]["structured_payload"]["effects"]["inventory_effects"]

    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()

    assert investigator_state["current_scene"]["title"] == "旅店走廊"
    assert investigator_state["own_character_state"]["current_sanity"] == 59
    assert investigator_state["own_character_state"]["inventory"] == ["破损徽章"]
    assert investigator_state["own_character_state"]["status_effects"] == ["紧张"]
    clue = investigator_state["scenario"]["clues"][0]
    assert clue["title"] == "地板灰痕"
    assert clue["status"] == "private_to_actor"
    assert clue["discovered_via"] == "investigate_floor"
    assert keeper_state["visible_authoritative_actions"][0]["source_type"] == "human_player"
    assert keeper_state["visible_authoritative_actions"][0]["applied_effects"]


def test_manual_authoritative_action_executes_and_checks_scene_preconditions(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "祭坛裂痕",
                        "text": "祭坛上的裂痕像是被某种钝器反复敲击留下的。",
                        "visibility_scope": "kp_only",
                    }
                ]
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    manual_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "keeper-1",
            "actor_type": "keeper",
            "action_text": "KP 手动推进到祭坛密室，并公开裂痕线索。",
            "structured_action": {"type": "manual_scene_push"},
            "effects": {
                "scene_transitions": [
                    {
                        "title": "祭坛密室",
                        "summary": "调查员被引导进入祭坛密室。",
                        "phase": "confrontation",
                        "required_current_phase": "setup",
                        "consequence_tags": ["警觉升级"],
                    }
                ],
                "clue_state_effects": [
                    {
                        "clue_title": "祭坛裂痕",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "keeper_reveal",
                    }
                ],
                "character_stat_effects": [{"actor_id": "investigator-1", "hp_delta": -2}],
                "visibility_effects": [
                    {
                        "target_kind": "clue",
                        "target_title": "祭坛裂痕",
                        "visibility_scope": "public",
                    }
                ],
            },
        },
    )
    assert manual_response.status_code == 202
    manual_payload = manual_response.json()

    assert manual_payload["authoritative_action"]["source_type"] == "manual_operator"
    assert manual_payload["authoritative_action"]["execution_summary"] is not None

    investigator_two = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()
    assert investigator_two["current_scene"]["title"] == "祭坛密室"
    assert investigator_two["scenario"]["clues"][0]["title"] == "祭坛裂痕"

    failed_manual_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "keeper-1",
            "actor_type": "keeper",
            "action_text": "再次按 setup 前提切换场景。",
            "structured_action": {"type": "manual_scene_push"},
            "effects": {
                "scene_transitions": [
                    {
                        "title": "二次切换",
                        "summary": "这次切换不应通过。",
                        "phase": "confrontation",
                        "required_current_phase": "setup",
                    }
                ]
            },
        },
    )
    assert failed_manual_response.status_code == 400
    assert "场景切换前提不满足" in failed_manual_response.json()["detail"]


def test_investigator_still_sees_non_review_manual_authoritative_outcome_after_metadata_filtering(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "clue_id": "clue.altar_crack",
                        "title": "祭坛裂痕",
                        "text": "祭坛上的裂痕像是被某种钝器反复敲击留下的。",
                        "visibility_scope": "kp_only",
                    }
                ]
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    manual_response = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "keeper-1",
            "actor_type": "keeper",
            "action_text": "KP 手动推进到祭坛密室，并公开裂痕线索。",
            "structured_action": {"type": "manual_scene_push"},
            "effects": {
                "scene_transitions": [
                    {
                        "title": "祭坛密室",
                        "summary": "调查员被引导进入祭坛密室。",
                        "phase": "confrontation",
                        "required_current_phase": "setup",
                        "consequence_tags": ["警觉升级"],
                    }
                ],
                "clue_state_effects": [
                    {
                        "clue_id": "clue.altar_crack",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "keeper_reveal",
                    }
                ],
            },
        },
    )
    assert manual_response.status_code == 202
    manual_payload = manual_response.json()
    authoritative_payload = manual_payload["authoritative_action"]
    event_payload = manual_payload["authoritative_event"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()
    snapshot_state = client.get(f"/sessions/{session_id}/snapshot").json()

    keeper_action = next(
        action
        for action in keeper_state["visible_authoritative_actions"]
        if action["action_id"] == authoritative_payload["action_id"]
    )
    investigator_action = next(
        action
        for action in investigator_state["visible_authoritative_actions"]
        if action["action_id"] == authoritative_payload["action_id"]
    )
    investigator_event = next(
        event
        for event in investigator_state["visible_events"]
        if event["event_id"] == event_payload["event_id"]
    )

    assert keeper_action["source_type"] == "manual_operator"
    assert keeper_action["review_id"] is None
    assert keeper_action["draft_id"] is None

    assert investigator_state["visible_draft_actions"] == []
    assert investigator_state["visible_reviewed_actions"] == []
    assert investigator_action["action_id"] == authoritative_payload["action_id"]
    assert investigator_action["source_type"] == "manual_operator"
    assert investigator_action["text"] == "KP 手动推进到祭坛密室，并公开裂痕线索。"
    assert investigator_action["review_id"] is None
    assert investigator_action["draft_id"] is None
    assert investigator_action["execution_summary"] is not None
    assert investigator_event["text"] == "KP 手动推进到祭坛密室，并公开裂痕线索。"
    assert investigator_event["structured_payload"]["authoritative_action_id"] == authoritative_payload["action_id"]
    assert investigator_event["structured_payload"]["source_type"] == "manual_operator"
    assert investigator_event["structured_payload"]["effects"]["clue_state_effects"][0]["status"] == "shared_with_party"
    assert investigator_event["structured_payload"]["effects"]["clue_state_effects"][0]["discovered_via"] == "keeper_reveal"
    assert investigator_state["current_scene"]["title"] == "祭坛密室"
    assert investigator_state["scenario"]["clues"][0]["clue_id"] == "clue.altar_crack"
    assert investigator_state["scenario"]["clues"][0]["status"] == "shared_with_party"

    assert snapshot_state["authoritative_actions"][-1]["action_id"] == authoritative_payload["action_id"]
    assert snapshot_state["authoritative_actions"][-1]["source_type"] == "manual_operator"
    assert snapshot_state["authoritative_actions"][-1]["review_id"] is None
    assert snapshot_state["authoritative_actions"][-1]["draft_id"] is None
    assert snapshot_state["timeline"][-1]["event_id"] == event_payload["event_id"]
    assert snapshot_state["timeline"][-1]["structured_payload"]["authoritative_action_id"] == authoritative_payload["action_id"]
    assert snapshot_state["timeline"][-1]["structured_payload"]["source_type"] == "manual_operator"


def test_rollback_after_applied_authoritative_execution_restores_prior_state(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "门后抓痕",
                        "text": "门板背后的抓痕说明有什么东西曾被困在里面。",
                        "visibility_scope": "kp_only",
                    }
                ]
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
            "action_text": "我推开门并检查门后的抓痕。",
            "structured_action": {"type": "open_door"},
            "effects": {
                "scene_transitions": [
                    {
                        "title": "门后储物间",
                        "summary": "门后是狭窄的储物间。",
                        "phase": "investigation",
                    }
                ],
                "clue_state_effects": [
                    {
                        "clue_title": "门后抓痕",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "open_door",
                    }
                ],
                "inventory_effects": [
                    {"actor_id": "investigator-1", "add_items": ["铁门钥匙"]}
                ],
            },
        },
    )
    assert action_response.status_code == 202
    assert action_response.json()["state_version"] == 2

    rollback_response = client.post(
        f"/sessions/{session_id}/rollback",
        json={"target_version": 1},
    )
    assert rollback_response.status_code == 200

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    assert keeper_state["current_scene"]["title"] == "开场"
    assert keeper_state["visible_authoritative_actions"] == []
    assert investigator_state["own_character_state"]["inventory"] == []
    assert investigator_state["scenario"]["clues"] == []


def test_trigger_based_clue_progression_activates_discovery_and_fail_forward(
    client: TestClient,
) -> None:
    _register_spot_hidden_rule(client, source_id="trigger-rule")
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "窗边鞋印",
                        "text": "窗边鞋印指向有人曾从外墙翻窗进入。",
                        "visibility_scope": "kp_only",
                        "discovery_triggers": [
                            {
                                "action_types": ["investigate_search"],
                                "required_topic": "term:spot_hidden",
                                "reveal_to": "party",
                                "assign_to_actor": False,
                                "discovered_via": "search_scene",
                            }
                        ],
                    },
                    {
                        "title": "暗门划痕",
                        "text": "墙上的划痕提示这里原本有一道暗门。",
                        "visibility_scope": "kp_only",
                        "core_clue_flag": True,
                        "alternate_paths": ["图书馆使用旧图纸", "询问旅店木匠"],
                        "fail_forward_text": "即使侦查失败，也能通过墙面异常磨损逐步意识到这里不对劲。",
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
                    },
                ]
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
            "action_text": "我在窗边和墙面反复搜索痕迹。",
            "structured_action": {"type": "investigate_search"},
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert action_response.status_code == 202
    action_payload = action_response.json()["authoritative_action"]

    assert len(action_payload["effects"]["clue_state_effects"]) == 2
    assert any(effect["activate_fail_forward"] for effect in action_payload["effects"]["clue_state_effects"])

    investigator_one = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    investigator_two = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()

    actor_one_clues = {clue["title"]: clue for clue in investigator_one["scenario"]["clues"]}
    actor_two_clues = {clue["title"]: clue for clue in investigator_two["scenario"]["clues"]}

    assert actor_one_clues["窗边鞋印"]["status"] == "shared_with_party"
    assert actor_one_clues["窗边鞋印"]["discovered_via"] == "search_scene"
    assert actor_one_clues["暗门划痕"]["status"] == "private_to_actor"
    assert actor_one_clues["暗门划痕"]["discovered_via"] == "search_fail_forward"
    assert actor_two_clues["窗边鞋印"]["status"] == "shared_with_party"
    assert "暗门划痕" not in actor_two_clues


def test_ai_draft_effects_do_not_mutate_state_before_review(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "楼梯断痕",
                        "text": "楼梯扶手上的断痕像是某人匆忙下楼时留下的。",
                        "visibility_scope": "kp_only",
                    }
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
            "action_text": "我建议直接冲下楼梯并夺走线索。",
            "structured_action": {"type": "risky_suggestion"},
            "effects": {
                "scene_transitions": [
                    {
                        "title": "一楼楼梯口",
                        "summary": "草稿不应直接推动场景。",
                        "phase": "investigation",
                    }
                ],
                "clue_state_effects": [
                    {
                        "clue_title": "楼梯断痕",
                        "status": "shared_with_party",
                        "share_with_party": True,
                    }
                ],
                "inventory_effects": [
                    {"actor_id": "investigator-1", "add_items": ["不应出现的线索卡"]}
                ],
            },
        },
    )
    assert draft_response.status_code == 202
    draft_payload = draft_response.json()["draft_action"]
    assert draft_payload["review_status"] == "pending"
    assert draft_payload["effects"]["scene_transitions"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    assert keeper_state["current_scene"]["title"] == "开场"
    assert keeper_state["visible_authoritative_actions"] == []
    assert investigator_state["own_character_state"]["inventory"] == []
    assert investigator_state["scenario"]["clues"] == []
