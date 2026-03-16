from __future__ import annotations

import shutil

import coc_runner.application.session_service as session_service_module
from fastapi.testclient import TestClient
from coc_runner.domain.dice import D100Roll, RollOutcome

from coc_runner.domain.scenario_examples import whispering_guesthouse_payload
from tests.helpers import make_participant, make_scenario
from tests.test_session_import import (
    KEEPER_ID,
    _create_checkpoint,
    _get_snapshot,
    _import_character_sheet_source,
    _import_snapshot,
    _make_cross_environment_client,
    _start_snapshot_session,
)


def _register_runtime_source(
    client: TestClient,
    *,
    source_id: str,
    source_title_zh: str,
    content: str,
    source_kind: str = "campaign_note",
    default_priority: int = 30,
    is_authoritative: bool = False,
) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": "plain_text",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
            "default_priority": default_priority,
            "is_authoritative": is_authoritative,
        },
    )
    assert register_response.status_code == 201
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert ingest_response.status_code == 200


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


def _keeper_progression_scenario() -> dict:
    return make_scenario(
        clues=[
            {
                "clue_id": "clue-alpha",
                "title": "前台异常痕迹",
                "text": "它说明前台留言曾被人改动过。",
                "visibility_scope": "kp_only",
            },
            {
                "clue_id": "clue-beta",
                "title": "账册缺页编号",
                "text": "它说明缺页记录来自二楼住客登记册。",
                "visibility_scope": "kp_only",
            },
        ],
        beats=[
            {
                "beat_id": "beat-alpha",
                "title": "观察前台记账桌",
                "start_unlocked": True,
                "complete_conditions": {
                    "clue_discovered": {"clue_id": "clue-alpha"}
                },
                "next_beats": ["beat-beta"],
            },
            {
                "beat_id": "beat-beta",
                "title": "检查账册缺页",
                "complete_conditions": {
                    "clue_discovered": {"clue_id": "clue-beta"}
                },
                "next_beats": ["beat-gamma"],
            },
            {
                "beat_id": "beat-gamma",
                "title": "侧线测试节点",
            },
        ],
    )


def _keeper_no_next_beat_scenario() -> dict:
    return make_scenario(
        beats=[
            {
                "beat_id": "beat-solo",
                "title": "孤立当前节点",
                "start_unlocked": True,
            }
        ],
    )


def _start_keeper_progression_session(
    client: TestClient,
    *,
    scenario: dict | None = None,
) -> str:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "scenario": scenario or _keeper_progression_scenario(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def _advance_keeper_dashboard_session(client: TestClient, session_id: str) -> tuple[str, str]:
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
    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    prompt_id = keeper_state["keeper_workflow"]["active_prompts"][0]["prompt_id"]

    draft_response = client.post(
        f"/sessions/{session_id}/kp-draft",
        json={
            "draft_text": "KP 草稿：若调查员继续追问秦老板，应准备对话压力。",
            "structured_action": {"type": "kp_note"},
        },
    )
    assert draft_response.status_code == 202
    draft_id = draft_response.json()["draft_action"]["draft_id"]
    return prompt_id, draft_id


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
    assert "当前状态" in html
    assert "计划中" in html
    assert "planned" in html
    assert "找到能指向地窖的记录" in html
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in html
    assert "KP 草稿：若调查员继续追问秦老板，应准备对话压力。" in html
    assert "账房保留点" in html
    assert 'href="/playtest/sessions"' in html
    assert f'/playtest/sessions/{session_id}/home"' in html
    assert f'/playtest/sessions/{session_id}"' in html
    assert f'/sessions/{session_id}/snapshot"' in html
    assert f'/sessions/{session_id}/export"' in html
    assert html.index("我趁老板转身时抽出柜台后的旧图纸并溜进账房。") < html.index(
        "会话已创建：雾港旅店的低语"
    )


def test_keeper_dashboard_shows_lifecycle_control_for_default_planned_session(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "会话生命周期" in html
    assert 'id="lifecycle-control"' in html
    assert "当前状态" in html
    assert "计划中" in html
    assert "planned" in html
    assert (
        f'action="/playtest/sessions/{session_id}/keeper/lifecycle#lifecycle-control"'
        in html
    )
    assert 'name="target_status" value="active"' in html
    assert 'name="target_status" value="paused"' not in html
    assert 'name="target_status" value="completed"' not in html


def test_keeper_dashboard_attention_items_include_prompt_and_draft_jump_targets(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, draft_id = _advance_keeper_dashboard_session(client, session_id)

    response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert f'href="#prompt-{prompt_id}"' in html
    assert f'id="prompt-{prompt_id}"' in html
    assert "处理此提示" in html
    assert f"/sessions/{session_id}/keeper-prompts/{prompt_id}/status" in html
    assert f'href="#draft-{draft_id}"' in html
    assert f'id="draft-{draft_id}"' in html
    assert "前往审阅" in html
    assert f"/sessions/{session_id}/draft-actions/{draft_id}/review" in html
    assert "KP 草稿：若调查员继续追问秦老板，应准备对话压力。" in html


def test_keeper_dashboard_shows_live_control_entries_and_investigator_page_does_not(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")
    investigator_response = client.get(
        f"/playtest/sessions/{session_id}/investigator/investigator-1"
    )

    assert keeper_response.status_code == 200
    keeper_html = keeper_response.text
    assert "实时控场" in keeper_html
    assert "会话生命周期" in keeper_html
    assert "目标控制" in keeper_html
    assert "Reveal 控制" in keeper_html
    assert 'id="objective-control"' in keeper_html
    assert 'id="reveal-control"' in keeper_html
    assert 'id="objective-control-objective.lobby.observe_keeper"' in keeper_html
    assert (
        f'/playtest/sessions/{session_id}/keeper/objectives/objective.lobby.observe_keeper/complete#live-control"'
        in keeper_html
    )
    assert (
        f'/playtest/sessions/{session_id}/keeper/reveal/clues/clue.old_floorplan#live-control"'
        in keeper_html
    )
    assert (
        f'/playtest/sessions/{session_id}/keeper/reveal/scenes/scene.guesthouse_office#live-control"'
        in keeper_html
    )
    assert "标记完成" in keeper_html
    assert "公开线索" in keeper_html
    assert "公开场景" in keeper_html

    assert investigator_response.status_code == 200
    investigator_html = investigator_response.text
    assert "会话生命周期" not in investigator_html
    assert "实时控场" not in investigator_html
    assert "规则与知识辅助" not in investigator_html
    assert "/keeper/lifecycle" not in investigator_html
    assert "/keeper/objectives/" not in investigator_html
    assert "/keeper/reveal/" not in investigator_html


def test_keeper_dashboard_can_start_minimal_combat_context_and_advance_turn_order(
    client: TestClient,
) -> None:
    fast = make_participant("investigator-1", "林舟")
    fast["character"]["attributes"]["dexterity"] = 80
    medium = make_participant("ai-1", "测试调查员", kind="ai")
    medium["character"]["attributes"]["dexterity"] = 65
    slow = make_participant("investigator-2", "周岚")
    slow["character"]["attributes"]["dexterity"] = 45

    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "scenario": whispering_guesthouse_payload(),
            "participants": [fast, medium, slow],
        },
    )
    assert response.status_code == 201
    session_id = response.json()["session_id"]

    initial_response = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert initial_response.status_code == 200
    initial_html = initial_response.text
    assert "战斗流程" in initial_html
    assert "当前未建立战斗顺序。" in initial_html
    assert "当前只提供 very small 的行动顺序骨架" in initial_html

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200

    start_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID},
    )
    assert start_response.status_code == 200
    start_html = start_response.text
    assert "已建立战斗顺序" in start_html
    assert "当前行动者：林舟" in start_html
    assert "下一位：测试调查员" in start_html
    assert "第 1 轮" in start_html
    assert "林舟（DEX 80）" in start_html
    assert "测试调查员（DEX 65）" in start_html
    assert "周岚（DEX 45）" in start_html

    advance_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/advance",
        data={"operator_id": KEEPER_ID},
    )
    assert advance_response.status_code == 200
    advance_html = advance_response.text
    assert "已推进到下一位行动者" in advance_html
    assert "当前行动者：测试调查员" in advance_html
    assert "下一位：周岚" in advance_html


def test_keeper_dashboard_completed_lifecycle_clears_combat_context_and_hides_stale_combat_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200

    start_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID},
    )
    assert start_response.status_code == 200
    assert "当前行动者：" in start_response.text

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )

    assert complete_response.status_code == 200
    complete_html = complete_response.text
    assert "当前未建立战斗顺序。" in complete_html
    assert "当前行动者：" not in complete_html
    assert "下一位：" not in complete_html

    snapshot = _get_snapshot(client, session_id)
    assert snapshot["status"] == "completed"
    assert snapshot["combat_context"] is None

    investigator_response = client.get(f"/playtest/sessions/{session_id}/investigator/investigator-1")
    assert investigator_response.status_code == 200
    investigator_html = investigator_response.text
    assert "战斗摘要" in investigator_html
    assert "当前未进入战斗顺序。" in investigator_html
    assert "当前行动者：" not in investigator_html


def test_keeper_dashboard_advance_combat_turn_wraps_round_after_last_actor(
    client: TestClient,
) -> None:
    fast = make_participant("investigator-1", "林舟")
    fast["character"]["attributes"]["dexterity"] = 80
    medium = make_participant("ai-1", "测试调查员", kind="ai")
    medium["character"]["attributes"]["dexterity"] = 65
    slow = make_participant("investigator-2", "周岚")
    slow["character"]["attributes"]["dexterity"] = 45

    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "scenario": whispering_guesthouse_payload(),
            "participants": [fast, medium, slow],
        },
    )
    assert response.status_code == 201
    session_id = response.json()["session_id"]

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200

    start_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID},
    )
    assert start_response.status_code == 200

    for _ in range(3):
        advance_response = client.post(
            f"/playtest/sessions/{session_id}/keeper/combat/advance",
            data={"operator_id": KEEPER_ID},
        )
        assert advance_response.status_code == 200

    snapshot = _get_snapshot(client, session_id)
    combat_context = snapshot["combat_context"]
    assert combat_context["round_number"] == 2
    assert combat_context["current_actor_id"] == "investigator-1"
    assert combat_context["next_actor_id"] == "ai-1"


def test_keeper_dashboard_rejects_start_combat_context_when_session_is_not_active(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    before_planned_snapshot = _get_snapshot(client, session_id)
    planned_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID},
    )
    assert planned_response.status_code == 400
    planned_html = planned_response.text
    assert "操作失败" in planned_html
    assert "combat_context_invalid" in planned_html
    assert "只有进行中的会话才能建立战斗顺序。" in planned_html
    assert _get_snapshot(client, session_id) == before_planned_snapshot

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200
    pause_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "paused"},
    )
    assert pause_response.status_code == 200

    before_paused_snapshot = _get_snapshot(client, session_id)
    paused_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID},
    )
    assert paused_response.status_code == 400
    paused_html = paused_response.text
    assert "操作失败" in paused_html
    assert "combat_context_invalid" in paused_html
    assert "只有进行中的会话才能建立战斗顺序。" in paused_html
    assert _get_snapshot(client, session_id) == before_paused_snapshot

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )
    assert complete_response.status_code == 200

    before_completed_snapshot = _get_snapshot(client, session_id)
    completed_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID},
    )
    assert completed_response.status_code == 400
    completed_html = completed_response.text
    assert "操作失败" in completed_html
    assert "combat_context_invalid" in completed_html
    assert "本局已结束，当前页面不再建立新的战斗顺序。" in completed_html
    assert _get_snapshot(client, session_id) == before_completed_snapshot


def test_keeper_dashboard_displays_runtime_rules_and_knowledge_assistance_panel(
    client: TestClient,
) -> None:
    _register_runtime_source(
        client,
        source_id="keeper-runtime-rules",
        source_title_zh="侦查规则",
        content="# 侦查\n侦查用于发现隐藏线索与可疑痕迹。",
        source_kind="rulebook",
        default_priority=40,
        is_authoritative=True,
    )
    _register_runtime_source(
        client,
        source_id="keeper-runtime-notes",
        source_title_zh="旅店笔记",
        content="稳住老板并找到账房线索时，优先核对留言页码与账册涂改痕迹。",
        source_kind="campaign_note",
        default_priority=25,
        is_authoritative=False,
    )
    session_id = _start_keeper_dashboard_session(client)
    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我按侦查规则检查前台记账桌的异常痕迹。",
            "structured_action": {"type": "inspect_front_desk"},
            "rules_query_text": "侦察能发现隐藏线索吗",
            "deterministic_resolution_required": True,
        },
    )
    assert action_response.status_code == 202

    response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "规则与知识辅助" in html
    assert "当前相关规则提示" in html
    assert "当前相关知识摘要" in html
    assert "侦察能发现隐藏线索吗" in html
    assert "发现隐藏线索与可疑痕迹" in html
    assert "旅店笔记" in html
    assert "稳住老板并找到账房线索时，优先核对留言页码与账册涂改痕迹。" in html


def test_keeper_dashboard_shows_san_aftermath_items_after_investigator_san_loss(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert keeper_response.status_code == 200
    html = keeper_response.text
    assert "理智后续待裁定" in html
    assert "林舟：黄衣之王的近距离显现" in html
    assert "SAN 60 -&gt; 57（损失 3）" in html
    assert "状态：待处理" in html
    assert 'category: <span class="mono">san_aftermath</span>' in html
    assert "处理此理智后续" in html


def test_keeper_dashboard_shows_contextual_san_aftermath_suggestions_without_auto_adjudication(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert keeper_response.status_code == 200
    html = keeper_response.text
    assert "建议参考" in html
    assert "以下建议仅供参考，最终仍由 KP 手动裁定。" in html
    assert "建议标签：惊惧失措" in html
    assert "建议标签：偏执警觉" in html
    assert "建议标签：强迫性回避" in html
    assert "“黄衣之王的近距离显现”直接造成了 3 点理智冲击" in html
    assert "角色职业“记者”可能会把这次冲击放大为过度警觉" in html
    assert "当前场景“雾港旅店大堂”与节点“稳住老板并找到账房线索”仍在持续施压" in html
    assert 'value="惊惧失措"' not in html
    assert 'value="偏执警觉"' not in html
    assert 'value="强迫性回避"' not in html
    assert "自动随机疯狂结果" not in html


def test_keeper_dashboard_can_acknowledge_san_aftermath_item_with_optional_note(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    note = "短暂惊惧，继续观察。"

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert keeper_state.status_code == 200
    prompt_id = next(
        prompt["prompt_id"]
        for prompt in keeper_state.json()["keeper_workflow"]["active_prompts"]
        if prompt.get("category") == "san_aftermath"
    )

    acknowledge_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "acknowledged",
            "note": note,
        },
    )

    assert acknowledge_response.status_code == 200
    html = acknowledge_response.text
    assert "KP 提示已更新" in html
    assert "理智后续待裁定" in html
    assert "林舟：黄衣之王的近距离显现" in html
    assert "状态：已确认" in html
    assert note in html


def test_keeper_dashboard_can_complete_san_aftermath_with_manual_adjudication_fields(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    note = "见到黄衣残影后短暂失措，接下来避免直视目标。"

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert keeper_state.status_code == 200
    prompt_id = next(
        prompt["prompt_id"]
        for prompt in keeper_state.json()["progress_state"]["queued_kp_prompts"]
        if prompt.get("category") == "san_aftermath"
    )

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "completed",
            "aftermath_label": "偏执怀疑",
            "duration_rounds": "3",
            "note": note,
        },
    )

    assert complete_response.status_code == 200
    html = complete_response.text
    assert "理智后续待裁定" in html
    assert "林舟：黄衣之王的近距离显现" in html
    assert "状态：已完成" in html
    assert "后续标签：偏执怀疑" in html
    assert "持续：3 回合" in html
    assert note in html
    assert "自动随机疯狂结果" not in html


def test_keeper_dashboard_requires_manual_adjudication_fields_to_complete_san_aftermath(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    )
    assert keeper_state.status_code == 200
    prompt_id = next(
        prompt["prompt_id"]
        for prompt in keeper_state.json()["progress_state"]["queued_kp_prompts"]
        if prompt.get("category") == "san_aftermath"
    )

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "completed",
        },
    )

    assert complete_response.status_code == 400
    html = complete_response.text
    assert "理智后续裁定在标记完成前必须填写后续标签和持续回合" in html
    assert "状态：待处理" in html
    assert "后续标签：" not in html
    assert 'class="meta-line">持续：' not in html


def test_keeper_dashboard_can_maintain_character_and_scene_hook_materials_without_external_card_dependency(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    dashboard_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert dashboard_response.status_code == 200
    dashboard_html = dashboard_response.text
    assert "钩子素材" in dashboard_html
    assert 'name="actor_id"' in dashboard_html
    assert 'name="scene_id"' in dashboard_html
    assert 'value="investigator-1"' in dashboard_html
    assert 'value="scene.guesthouse_lobby"' in dashboard_html
    assert 'value="npc.innkeeper"' not in dashboard_html

    character_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "hook_label": "文字残响",
            "hook_text": "看到破碎文字或反光边缘时，容易短暂盯住细节不放。",
        },
    )
    assert character_response.status_code == 200
    character_html = character_response.text
    assert "角色钩子素材已保存" in character_html
    assert "文字残响" in character_html
    assert "看到破碎文字或反光边缘时，容易短暂盯住细节不放。" in character_html

    scene_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/scenes",
        data={
            "operator_id": KEEPER_ID,
            "scene_id": "scene.guesthouse_lobby",
            "hook_label": "灯影压迫",
            "hook_text": "大堂煤气灯和柜台反光会把异常显现放大成持续压迫感。",
        },
    )
    assert scene_response.status_code == 200
    scene_html = scene_response.text
    assert "场景钩子素材已保存" in scene_html
    assert "灯影压迫" in scene_html
    assert "大堂煤气灯和柜台反光会把异常显现放大成持续压迫感。" in scene_html

    snapshot = _get_snapshot(client, session_id)
    investigator = next(
        participant
        for participant in snapshot["participants"]
        if participant["actor_id"] == "investigator-1"
    )
    lobby_scene = next(
        scene
        for scene in snapshot["scenario"]["scenes"]
        if scene["scene_id"] == "scene.guesthouse_lobby"
    )
    assert investigator["imported_character_source_id"] is None
    assert investigator["suggestion_hooks"] == [
        {
            "hook_id": investigator["suggestion_hooks"][0]["hook_id"],
            "hook_label": "文字残响",
            "hook_text": "看到破碎文字或反光边缘时，容易短暂盯住细节不放。",
            "created_at": investigator["suggestion_hooks"][0]["created_at"],
            "updated_at": investigator["suggestion_hooks"][0]["updated_at"],
        }
    ]
    assert lobby_scene["suggestion_hooks"] == [
        {
            "hook_id": lobby_scene["suggestion_hooks"][0]["hook_id"],
            "hook_label": "灯影压迫",
            "hook_text": "大堂煤气灯和柜台反光会把异常显现放大成持续压迫感。",
            "created_at": lobby_scene["suggestion_hooks"][0]["created_at"],
            "updated_at": lobby_scene["suggestion_hooks"][0]["updated_at"],
        }
    ]


def test_keeper_dashboard_san_suggestions_prefer_character_and_scene_hook_materials(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    character_label = "文字残响"
    character_text = "看到破碎文字或反光边缘时，容易短暂盯住细节不放。"
    scene_label = "灯影压迫"
    scene_text = "大堂煤气灯和柜台反光会把异常显现放大成持续压迫感。"

    character_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "hook_label": character_label,
            "hook_text": character_text,
        },
    )
    assert character_response.status_code == 200

    scene_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/scenes",
        data={
            "operator_id": KEEPER_ID,
            "scene_id": "scene.guesthouse_lobby",
            "hook_label": scene_label,
            "hook_text": scene_text,
        },
    )
    assert scene_response.status_code == 200

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert keeper_response.status_code == 200
    html = keeper_response.text
    assert f"建议标签：{character_label}" in html
    assert f"角色钩子：{character_text}" in html
    assert f"建议标签：{scene_label}" in html
    assert f"场景钩子：{scene_text}" in html
    assert f'value="{character_label}"' not in html
    assert f'value="{scene_label}"' not in html
    assert "自动随机疯狂结果" not in html


def test_keeper_dashboard_can_seed_character_and_scene_hook_materials_from_current_context(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    dashboard_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert dashboard_response.status_code == 200
    dashboard_html = dashboard_response.text
    assert "钩子素材" in dashboard_html
    assert "不会读取 Excel 或 scenario 文件夹" in dashboard_html
    assert "从当前角色上下文生成初始钩子" in dashboard_html
    assert "从当前场景上下文生成初始钩子" in dashboard_html

    character_seed_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters/seed",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
        },
    )
    assert character_seed_response.status_code == 200
    character_seed_html = character_seed_response.text
    assert "已从当前角色上下文生成初始钩子" in character_seed_html
    assert "职业钩子：记者" in character_seed_html
    assert "记者的职业视角会放大对异常线索与失序叙述的敏感度。" in character_seed_html

    scene_seed_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/scenes/seed",
        data={
            "operator_id": KEEPER_ID,
            "scene_id": "scene.guesthouse_lobby",
        },
    )
    assert scene_seed_response.status_code == 200
    scene_seed_html = scene_seed_response.text
    assert "已从当前场景上下文生成初始钩子" in scene_seed_html
    assert "场景钩子：雾港旅店大堂" in scene_seed_html
    assert "雾港旅店大堂的压抑氛围会放大异常显现带来的不安。" in scene_seed_html

    snapshot = _get_snapshot(client, session_id)
    investigator = next(
        participant
        for participant in snapshot["participants"]
        if participant["actor_id"] == "investigator-1"
    )
    lobby_scene = next(
        scene
        for scene in snapshot["scenario"]["scenes"]
        if scene["scene_id"] == "scene.guesthouse_lobby"
    )
    assert investigator["imported_character_source_id"] is None
    assert investigator["suggestion_hooks"] == [
        {
            "hook_id": investigator["suggestion_hooks"][0]["hook_id"],
            "hook_label": "职业钩子：记者",
            "hook_text": "记者的职业视角会放大对异常线索与失序叙述的敏感度。",
            "created_at": investigator["suggestion_hooks"][0]["created_at"],
            "updated_at": investigator["suggestion_hooks"][0]["updated_at"],
        }
    ]
    assert lobby_scene["suggestion_hooks"] == [
        {
            "hook_id": lobby_scene["suggestion_hooks"][0]["hook_id"],
            "hook_label": "场景钩子：雾港旅店大堂",
            "hook_text": "雾港旅店大堂的压抑氛围会放大异常显现带来的不安。",
            "created_at": lobby_scene["suggestion_hooks"][0]["created_at"],
            "updated_at": lobby_scene["suggestion_hooks"][0]["updated_at"],
        }
    ]


def test_keeper_dashboard_san_suggestions_can_read_seeded_hook_materials_without_file_sync(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    character_seed_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters/seed",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
        },
    )
    assert character_seed_response.status_code == 200

    scene_seed_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/scenes/seed",
        data={
            "operator_id": KEEPER_ID,
            "scene_id": "scene.guesthouse_lobby",
        },
    )
    assert scene_seed_response.status_code == 200

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert keeper_response.status_code == 200
    html = keeper_response.text
    assert "建议参考" in html
    assert "建议标签：职业钩子：记者" in html
    assert "角色钩子：记者的职业视角会放大对异常线索与失序叙述的敏感度。" in html
    assert "建议标签：场景钩子：雾港旅店大堂" in html
    assert "场景钩子：雾港旅店大堂的压抑氛围会放大异常显现带来的不安。" in html
    assert 'name="aftermath_label"' in html
    assert 'value="职业钩子：记者"' not in html
    assert 'value="场景钩子：雾港旅店大堂"' not in html
    assert "自动随机疯狂结果" not in html


def test_keeper_dashboard_can_import_external_character_and_scene_hook_materials_without_file_system_dependency(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    dashboard_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert dashboard_response.status_code == 200
    dashboard_html = dashboard_response.text
    assert "只接收已解析结果/sidecar 字段" in dashboard_html
    assert 'name="parsed_occupation"' in dashboard_html
    assert 'name="parsed_notes"' in dashboard_html
    assert 'name="parsed_title"' in dashboard_html
    assert 'name="parsed_context"' in dashboard_html
    assert 'value="npc.innkeeper"' not in dashboard_html

    character_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters/import",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "parsed_occupation": "记者",
            "parsed_notes": "长期追踪失踪报道，见到类似黄衣残影时会下意识追查到底。",
            "seed_hint": "追踪执念",
        },
    )
    assert character_response.status_code == 200
    character_html = character_response.text
    assert "已导入外部角色 hook seed" in character_html
    assert "追踪执念" in character_html
    assert "记者：长期追踪失踪报道，见到类似黄衣残影时会下意识追查到底。" in character_html

    scene_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/scenes/import",
        data={
            "operator_id": KEEPER_ID,
            "scene_id": "scene.guesthouse_lobby",
            "parsed_title": "雾港旅店大堂",
            "parsed_context": "煤气灯与柜台反光会把前台附近的异常显现放大成持续压迫。",
            "seed_hint": "灯影压迫 sidecar",
        },
    )
    assert scene_response.status_code == 200
    scene_html = scene_response.text
    assert "已导入外部场景 hook seed" in scene_html
    assert "灯影压迫 sidecar" in scene_html
    assert "煤气灯与柜台反光会把前台附近的异常显现放大成持续压迫。" in scene_html

    snapshot = _get_snapshot(client, session_id)
    investigator = next(
        participant
        for participant in snapshot["participants"]
        if participant["actor_id"] == "investigator-1"
    )
    lobby_scene = next(
        scene
        for scene in snapshot["scenario"]["scenes"]
        if scene["scene_id"] == "scene.guesthouse_lobby"
    )
    assert investigator["imported_character_source_id"] is None
    assert investigator["suggestion_hooks"] == [
        {
            "hook_id": investigator["suggestion_hooks"][0]["hook_id"],
            "hook_label": "追踪执念",
            "hook_text": "记者：长期追踪失踪报道，见到类似黄衣残影时会下意识追查到底。",
            "created_at": investigator["suggestion_hooks"][0]["created_at"],
            "updated_at": investigator["suggestion_hooks"][0]["updated_at"],
        }
    ]
    assert lobby_scene["suggestion_hooks"] == [
        {
            "hook_id": lobby_scene["suggestion_hooks"][0]["hook_id"],
            "hook_label": "灯影压迫 sidecar",
            "hook_text": "煤气灯与柜台反光会把前台附近的异常显现放大成持续压迫。",
            "created_at": lobby_scene["suggestion_hooks"][0]["created_at"],
            "updated_at": lobby_scene["suggestion_hooks"][0]["updated_at"],
        }
    ]


def test_keeper_dashboard_san_suggestions_can_read_imported_hook_materials_from_external_seed(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    character_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters/import",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "parsed_occupation": "记者",
            "parsed_notes": "长期追踪失踪报道，见到类似黄衣残影时会下意识追查到底。",
            "seed_hint": "追踪执念",
        },
    )
    assert character_response.status_code == 200

    scene_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/scenes/import",
        data={
            "operator_id": KEEPER_ID,
            "scene_id": "scene.guesthouse_lobby",
            "parsed_title": "雾港旅店大堂",
            "parsed_context": "煤气灯与柜台反光会把前台附近的异常显现放大成持续压迫。",
            "seed_hint": "灯影压迫 sidecar",
        },
    )
    assert scene_response.status_code == 200

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert keeper_response.status_code == 200
    html = keeper_response.text
    assert "建议参考" in html
    assert "建议标签：追踪执念" in html
    assert "角色钩子：记者：长期追踪失踪报道，见到类似黄衣残影时会下意识追查到底。" in html
    assert "建议标签：灯影压迫 sidecar" in html
    assert "场景钩子：煤气灯与柜台反光会把前台附近的异常显现放大成持续压迫。" in html
    assert 'name="aftermath_label"' in html
    assert 'value="追踪执念"' not in html
    assert 'value="灯影压迫 sidecar"' not in html
    assert "自动随机疯狂结果" not in html


def test_keeper_dashboard_can_import_character_hook_from_template_card_extraction(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    source_id = "character-sheet-template-hook-import"
    _import_character_sheet_source(client, source_id=source_id)

    dashboard_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert dashboard_response.status_code == 200
    dashboard_html = dashboard_response.text
    assert "固定模板卡导入只接收已解析好的 source_id" in dashboard_html
    assert 'name="template_source_id"' in dashboard_html
    assert 'type="file"' not in dashboard_html
    assert 'value="npc.innkeeper"' not in dashboard_html

    import_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters/import-template",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "template_source_id": source_id,
            "seed_hint": "模板卡执念",
        },
    )

    assert import_response.status_code == 200
    html = import_response.text
    assert "已从固定模板卡解析结果导入角色 hook" in html
    assert "模板卡执念" in html
    assert "总裁：" in html
    assert "小秘密：腹部的一条刀疤" in html

    snapshot = _get_snapshot(client, session_id)
    investigator = next(
        participant
        for participant in snapshot["participants"]
        if participant["actor_id"] == "investigator-1"
    )
    assert investigator["imported_character_source_id"] is None
    assert investigator["suggestion_hooks"] == [
        {
            "hook_id": investigator["suggestion_hooks"][0]["hook_id"],
            "hook_label": "模板卡执念",
            "hook_text": investigator["suggestion_hooks"][0]["hook_text"],
            "created_at": investigator["suggestion_hooks"][0]["created_at"],
            "updated_at": investigator["suggestion_hooks"][0]["updated_at"],
        }
    ]
    assert "总裁：" in investigator["suggestion_hooks"][0]["hook_text"]
    assert "小秘密：腹部的一条刀疤" in investigator["suggestion_hooks"][0]["hook_text"]


def test_keeper_dashboard_san_suggestions_can_read_template_card_imported_hook_materials(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    source_id = "character-sheet-template-hook-suggestion"
    _import_character_sheet_source(client, source_id=source_id)

    import_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/hooks/characters/import-template",
        data={
            "operator_id": KEEPER_ID,
            "actor_id": "investigator-1",
            "template_source_id": source_id,
            "seed_hint": "模板卡执念",
        },
    )
    assert import_response.status_code == 200

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    san_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )
    assert san_response.status_code == 200

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert keeper_response.status_code == 200
    html = keeper_response.text
    assert "建议参考" in html
    assert "建议标签：模板卡执念" in html
    assert "角色钩子：总裁：" in html
    assert "小秘密：腹部的一条刀疤" in html
    assert 'value="模板卡执念"' not in html
    assert "自动随机疯狂结果" not in html


def test_keeper_dashboard_lifecycle_controls_transition_status_and_render_closeout_summary(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )

    assert activate_response.status_code == 200
    activate_html = activate_response.text
    assert "会话状态已切换为进行中" in activate_html
    assert "当前状态" in activate_html
    assert "进行中" in activate_html
    assert "active" in activate_html
    assert 'name="target_status" value="paused"' in activate_html
    assert 'name="target_status" value="completed"' in activate_html
    assert "最近控场结果" in activate_html
    assert '控场类型：<span class="mono">Session 状态</span>' in activate_html
    assert 'href="#lifecycle-control"' in activate_html

    pause_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "paused"},
    )

    assert pause_response.status_code == 200
    pause_html = pause_response.text
    assert "会话状态已切换为已暂停" in pause_html
    assert "已暂停" in pause_html
    assert "paused" in pause_html
    assert 'name="target_status" value="active"' in pause_html
    assert 'name="target_status" value="completed"' in pause_html

    resume_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )

    assert resume_response.status_code == 200
    resume_html = resume_response.text
    assert "会话状态已切换为进行中" in resume_html
    assert "进行中" in resume_html
    assert "active" in resume_html

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )

    assert complete_response.status_code == 200
    complete_html = complete_response.text
    assert "会话状态已切换为已完成" in complete_html
    assert "当前状态" in complete_html
    assert "已完成" in complete_html
    assert "completed" in complete_html
    assert "本局收尾摘要" in complete_html
    assert "当前场景：" in complete_html
    assert "当前 beat：" in complete_html
    assert "调查员数量：1" in complete_html
    assert "检查点数量：0" in complete_html
    assert 'href="#lifecycle-control"' in complete_html
    assert 'name="target_status"' not in complete_html


def test_keeper_dashboard_invalid_lifecycle_transition_renders_structured_error_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "paused"},
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "session_lifecycle_invalid" in html
    assert "会话状态不能从计划中切换为已暂停" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot == before_snapshot


def test_keeper_dashboard_completed_session_rejects_objective_control_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    objective_id = "objective.lobby.observe_keeper"

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200
    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )
    assert complete_response.status_code == 200
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/objectives/{objective_id}/complete",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "keeper_live_control_invalid" in html
    assert "当前会话已完成，不能继续执行实时控场操作。" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot == before_snapshot


def test_keeper_dashboard_completed_session_rejects_beat_progression_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_progression_session(client)

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200
    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )
    assert complete_response.status_code == 200
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/beats/beat-beta/advance",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "keeper_live_control_invalid" in html
    assert "当前会话已完成，不能继续执行实时控场操作。" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot == before_snapshot


def test_keeper_dashboard_shows_beat_progression_block_with_legal_next_beat_candidates(
    client: TestClient,
) -> None:
    session_id = _start_keeper_progression_session(client)

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")
    investigator_response = client.get(
        f"/playtest/sessions/{session_id}/investigator/investigator-1"
    )

    assert keeper_response.status_code == 200
    keeper_html = keeper_response.text
    assert "Beat 推进" in keeper_html
    assert 'id="beat-progression"' in keeper_html
    assert 'id="beat-progression-current-beat-alpha"' in keeper_html
    assert "当前 beat：beat-alpha" in keeper_html
    assert "观察前台记账桌" in keeper_html
    assert "检查账册缺页" in keeper_html
    assert (
        f'/playtest/sessions/{session_id}/keeper/beats/beat-beta/advance#beat-progression"'
        in keeper_html
    )
    assert "推进到此 beat" in keeper_html
    assert f"/playtest/sessions/{session_id}/keeper/beats/beat-gamma/advance" not in keeper_html

    assert investigator_response.status_code == 200
    investigator_html = investigator_response.text
    assert "Beat 推进" not in investigator_html
    assert "/keeper/beats/" not in investigator_html


def test_keeper_dashboard_advances_to_legal_next_beat_and_rerenders_with_feedback(
    client: TestClient,
) -> None:
    session_id = _start_keeper_progression_session(client)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/beats/beat-beta/advance",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 200
    html = response.text
    assert "已推进到下一 beat：检查账册缺页" in html
    assert "当前 beat：beat-beta" in html
    assert 'id="beat-progression-current-beat-beta"' in html
    assert "最近控场结果" in html
    assert '控场类型：<span class="mono">Beat 推进</span>' in html
    assert "回到 beat 推进" in html
    assert 'href="#beat-progression-current-beat-beta"' in html


def test_keeper_dashboard_shows_empty_beat_progression_state_when_no_next_beat_candidates(
    client: TestClient,
) -> None:
    session_id = _start_keeper_progression_session(
        client,
        scenario=_keeper_no_next_beat_scenario(),
    )

    response = client.get(f"/playtest/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "Beat 推进" in html
    assert "当前 beat 没有可手动推进的合法下一节点。" in html
    assert "/keeper/beats/" not in html


def test_keeper_dashboard_invalid_beat_progression_renders_structured_error_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_progression_session(client)
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/beats/beat-gamma/advance",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "keeper_live_control_invalid" in html
    assert "当前 beat 不能直接推进到“侧线测试节点”" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot == before_snapshot


def test_keeper_dashboard_objective_complete_and_reopen_controls_rerender_with_feedback(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    objective_id = "objective.lobby.observe_keeper"

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/objectives/{objective_id}/complete",
        data={"operator_id": KEEPER_ID},
    )

    assert complete_response.status_code == 200
    complete_html = complete_response.text
    assert "已手动标记目标完成" in complete_html
    assert "确认老板是否在刻意回避储物间问题" in complete_html
    assert "最近控场结果" in complete_html
    assert '控场类型：<span class="mono">Objective 已完成</span>' in complete_html
    assert "未完成目标：0" in complete_html
    assert "最近完成目标：确认老板是否在刻意回避储物间问题" in complete_html
    assert 'href="#objective-control-objective.lobby.observe_keeper"' in complete_html
    assert "回到 objective 控制" in complete_html
    assert (
        f'/playtest/sessions/{session_id}/keeper/objectives/{objective_id}/reopen#live-control"'
        in complete_html
    )

    reopen_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/objectives/{objective_id}/reopen",
        data={"operator_id": KEEPER_ID},
    )

    assert reopen_response.status_code == 200
    reopen_html = reopen_response.text
    assert "已取消目标完成状态" in reopen_html
    assert "确认老板是否在刻意回避储物间问题" in reopen_html
    assert "未完成目标：1" in reopen_html
    assert '控场类型：<span class="mono">Objective 已恢复未完成</span>' in reopen_html
    assert 'href="#objective-control-objective.lobby.observe_keeper"' in reopen_html
    assert "最近完成目标：确认老板是否在刻意回避储物间问题" not in reopen_html
    assert (
        f'/playtest/sessions/{session_id}/keeper/objectives/{objective_id}/complete#live-control"'
        in reopen_html
    )


def test_keeper_dashboard_invalid_objective_control_renders_structured_error_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/objectives/objective.missing/complete",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "keeper_live_control_objective_not_found" in html
    assert "未找到目标 objective.missing" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot == before_snapshot


def test_keeper_dashboard_reveal_clue_and_scene_controls_apply_and_surface_results(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    clue_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/reveal/clues/clue.old_floorplan",
        data={"operator_id": KEEPER_ID},
    )

    assert clue_response.status_code == 200
    clue_html = clue_response.text
    assert "已公开线索" in clue_html
    assert "旅店旧图纸" in clue_html
    assert "最近控场结果" in clue_html
    assert '控场类型：<span class="mono">Reveal 线索</span>' in clue_html
    assert "已公开线索：旅店旧图纸" in clue_html
    assert 'href="#reveal-control"' in clue_html
    assert "回到 reveal 控制" in clue_html
    assert (
        f'/playtest/sessions/{session_id}/keeper/reveal/clues/clue.old_floorplan#live-control"'
        not in clue_html
    )

    investigator_page = client.get(
        f"/playtest/sessions/{session_id}/investigator/investigator-1"
    )
    assert investigator_page.status_code == 200
    assert "旅店旧图纸" in investigator_page.text

    scene_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/reveal/scenes/scene.guesthouse_office",
        data={"operator_id": KEEPER_ID},
    )

    assert scene_response.status_code == 200
    scene_html = scene_response.text
    assert "已公开场景" in scene_html
    assert "旅店账房" in scene_html
    assert "最近控场结果" in scene_html
    assert '控场类型：<span class="mono">Reveal 场景</span>' in scene_html
    assert "已公开场景：旅店账房" in scene_html
    assert "找到能指向地窖的记录" in scene_html
    assert 'href="#reveal-control"' in scene_html
    assert (
        f'/playtest/sessions/{session_id}/keeper/reveal/scenes/scene.guesthouse_office#live-control"'
        not in scene_html
    )

    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "investigator", "viewer_id": "investigator-1"},
    )
    assert investigator_state.status_code == 200
    visible_scene_ids = {
        scene["scene_id"] for scene in investigator_state.json()["scenario"]["scenes"]
    }
    assert "scene.guesthouse_office" in visible_scene_ids


def test_keeper_dashboard_invalid_reveal_control_renders_structured_error_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/reveal/scenes/scene.missing",
        data={"operator_id": KEEPER_ID},
    )

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "keeper_live_control_scene_not_found" in html
    assert "未找到场景 scene.missing" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot == before_snapshot


def test_keeper_dashboard_prompt_target_supports_acknowledge_and_completed_form_submission(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)

    dashboard_response = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert dashboard_response.status_code == 200
    dashboard_html = dashboard_response.text
    assert (
        f'action="/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status#prompt-{prompt_id}"'
        in dashboard_html
    )
    assert 'name="note"' in dashboard_html
    assert 'name="status" value="acknowledged"' in dashboard_html
    assert 'name="status" value="completed"' in dashboard_html
    assert 'name="status" value="dismissed"' in dashboard_html

    acknowledge_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "acknowledged",
            "note": "先记下老板失态，再决定是否继续追问。",
        },
    )
    assert acknowledge_response.status_code == 200
    acknowledge_html = acknowledge_response.text
    assert "KP 提示已更新" in acknowledge_html
    assert f'id="prompt-{prompt_id}"' in acknowledge_html
    assert "acknowledged" in acknowledge_html
    assert "先记下老板失态，再决定是否继续追问。" in acknowledge_html

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "completed",
            "note": "提示已处理完毕，可继续推进账房调查。",
        },
    )
    assert complete_response.status_code == 200
    complete_html = complete_response.text
    assert "KP 提示已更新" in complete_html
    assert "提示已处理完毕，可继续推进账房调查。" in complete_html
    assert "当前没有待处理的 KP 提示。" in complete_html
    assert f'id="prompt-{prompt_id}"' not in complete_html


def test_keeper_dashboard_prompt_target_supports_dismissed_with_optional_note(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)

    dismiss_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "dismissed",
            "note": "本条先不处理，后续由人工场景演绎覆盖。",
        },
    )
    assert dismiss_response.status_code == 200
    dismiss_html = dismiss_response.text
    assert "KP 提示已更新" in dismiss_html
    assert "本条先不处理，后续由人工场景演绎覆盖。" in dismiss_html
    assert "当前没有待处理的 KP 提示。" in dismiss_html
    assert f'id="prompt-{prompt_id}"' not in dismiss_html


def test_keeper_dashboard_prompt_target_still_supports_submission_without_note(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={"operator_id": KEEPER_ID, "status": "acknowledged"},
    )

    assert response.status_code == 200
    html = response.text
    assert "KP 提示已更新" in html
    assert f'id="prompt-{prompt_id}"' in html
    assert "acknowledged" in html


def test_keeper_dashboard_recent_results_persist_acknowledged_prompt_note_after_refresh(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)
    note = "先记为已知风险，等调查员继续施压时再处理。"

    update_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={"operator_id": KEEPER_ID, "status": "acknowledged", "note": note},
    )
    assert update_response.status_code == 200

    refreshed = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert refreshed.status_code == 200
    html = refreshed.text
    assert "最近处理结果" in html
    assert "最近提示结果" in html
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in html
    assert "acknowledged" in html
    assert note in html


def test_keeper_dashboard_recent_results_persist_completed_prompt_note_after_refresh(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)
    note = "提示已处理完毕，直接进入下一个应对分支。"

    update_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={"operator_id": KEEPER_ID, "status": "completed", "note": note},
    )
    assert update_response.status_code == 200

    refreshed = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert refreshed.status_code == 200
    html = refreshed.text
    assert "最近处理结果" in html
    assert "最近提示结果" in html
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in html
    assert "completed" in html
    assert note in html
    assert f'id="prompt-{prompt_id}"' not in html


def test_keeper_dashboard_recent_results_persist_dismissed_prompt_note_after_refresh(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)
    note = "这条提示先关闭，由 KP 自行演绎处理。"

    update_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={"operator_id": KEEPER_ID, "status": "dismissed", "note": note},
    )
    assert update_response.status_code == 200

    refreshed = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert refreshed.status_code == 200
    html = refreshed.text
    assert "最近处理结果" in html
    assert "最近提示结果" in html
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in html
    assert "dismissed" in html
    assert note in html


def test_keeper_dashboard_prompt_update_failure_renders_structured_error(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    prompt_id, _ = _advance_keeper_dashboard_session(client, session_id)

    first_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={"operator_id": KEEPER_ID, "status": "completed"},
    )
    assert first_response.status_code == 200

    failed_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={"operator_id": KEEPER_ID, "status": "dismissed"},
    )
    assert failed_response.status_code == 400
    failed_html = failed_response.text
    assert "操作失败" in failed_html
    assert "keeper_prompt_invalid" in failed_html
    assert f"KP 提示 {prompt_id} 已结束，不能再次变更状态" in failed_html


def test_keeper_dashboard_draft_target_supports_approve_submission_and_clears_pending_list(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _, draft_id = _advance_keeper_dashboard_session(client, session_id)

    dashboard_response = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert dashboard_response.status_code == 200
    dashboard_html = dashboard_response.text
    assert (
        f'action="/playtest/sessions/{session_id}/draft-actions/{draft_id}/review#draft-{draft_id}"'
        in dashboard_html
    )
    assert 'name="editor_notes"' in dashboard_html
    assert 'name="decision" value="approve"' in dashboard_html
    assert 'name="decision" value="reject"' in dashboard_html

    approve_response = client.post(
        f"/playtest/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={
            "reviewer_id": KEEPER_ID,
            "decision": "approve",
            "editor_notes": "这条建议可以直接通过，并作为后续口风基准。",
        },
    )
    assert approve_response.status_code == 200
    approve_html = approve_response.text
    assert "已批准草稿行动并写入权威历史" in approve_html
    assert "这条建议可以直接通过，并作为后续口风基准。" in approve_html
    assert "当前没有待审草稿。" in approve_html
    assert f'id="draft-{draft_id}"' not in approve_html


def test_keeper_dashboard_draft_reject_and_review_failure_render_feedback(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _, draft_id = _advance_keeper_dashboard_session(client, session_id)

    reject_response = client.post(
        f"/playtest/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={
            "reviewer_id": KEEPER_ID,
            "decision": "reject",
            "editor_notes": "先不要采用这条口风，等更多线索落地后再决定。",
        },
    )
    assert reject_response.status_code == 200
    reject_html = reject_response.text
    assert "已拒绝草稿行动，未写入权威历史" in reject_html
    assert "先不要采用这条口风，等更多线索落地后再决定。" in reject_html
    assert "当前没有待审草稿。" in reject_html

    failed_response = client.post(
        f"/playtest/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={"reviewer_id": KEEPER_ID, "decision": "approve"},
    )
    assert failed_response.status_code == 400
    failed_html = failed_response.text
    assert "操作失败" in failed_html
    assert "draft_review_invalid" in failed_html
    assert f"草稿 {draft_id} 当前不是待审核状态" in failed_html


def test_keeper_dashboard_draft_target_still_supports_review_without_editor_notes(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _, draft_id = _advance_keeper_dashboard_session(client, session_id)

    response = client.post(
        f"/playtest/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={"reviewer_id": KEEPER_ID, "decision": "approve"},
    )

    assert response.status_code == 200
    html = response.text
    assert "已批准草稿行动并写入权威历史" in html
    assert "当前没有待审草稿。" in html


def test_keeper_dashboard_recent_results_show_approved_draft_outcome_and_editor_notes_after_refresh(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _, draft_id = _advance_keeper_dashboard_session(client, session_id)
    note = "这条建议已转为正式落地结果，可作为后续口风。"

    review_response = client.post(
        f"/playtest/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={"reviewer_id": KEEPER_ID, "decision": "approve", "editor_notes": note},
    )
    assert review_response.status_code == 200

    refreshed = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert refreshed.status_code == 200
    html = refreshed.text
    assert "最近处理结果" in html
    assert "最近草稿结果" in html
    assert "KP 草稿：若调查员继续追问秦老板，应准备对话压力。" in html
    assert "approve" in html
    assert "已写入权威历史" in html
    assert note in html
    assert "落地摘要：" in html


def test_keeper_dashboard_recent_results_show_rejected_draft_outcome_and_editor_notes_after_refresh(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _, draft_id = _advance_keeper_dashboard_session(client, session_id)
    note = "先不要采用这条草稿，等更多线索落地后再说。"

    review_response = client.post(
        f"/playtest/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={"reviewer_id": KEEPER_ID, "decision": "reject", "editor_notes": note},
    )
    assert review_response.status_code == 200

    refreshed = client.get(f"/playtest/sessions/{session_id}/keeper")
    assert refreshed.status_code == 200
    html = refreshed.text
    assert "最近处理结果" in html
    assert "最近草稿结果" in html
    assert "KP 草稿：若调查员继续追问秦老板，应准备对话压力。" in html
    assert "reject" in html
    assert "未写入权威历史" in html
    assert note in html


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
    assert "规则与知识辅助" in html
    assert "当前局面还没有明显相关的规则提示。" in html
    assert "当前局面还没有明显相关的知识摘要。" in html
    assert "还没有最近处理结果。" in html
    assert "还没有检查点。先去创建一个用于回放或分支。" in html
    assert "当前环境缺少外部知识源" not in html
    assert "控场类型：" not in html
    assert 'href="#prompt-' not in html
    assert 'href="#draft-' not in html
    assert 'href="#objective-control-' not in html
    assert 'href="#reveal-control"' not in html
    assert f"/keeper/prompts/" not in html
    assert f"/draft-actions/" not in html


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
