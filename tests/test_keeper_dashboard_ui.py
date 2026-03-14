from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

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
    assert "/keeper/lifecycle" not in investigator_html
    assert "/keeper/objectives/" not in investigator_html
    assert "/keeper/reveal/" not in investigator_html


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
