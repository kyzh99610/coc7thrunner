from __future__ import annotations

from urllib.parse import quote

import coc_runner.application.session_service as session_service_module
from fastapi.testclient import TestClient
from coc_runner.application.dice_execution import LocalDiceExecutionBackend
from coc_runner.application.local_llm_service import (
    LocalLLMAssistantPayload,
    LocalLLMAssistantResult,
)
from coc_runner.domain.dice import D100Roll, RollOutcome
from coc_runner.domain.scenario_examples import whispering_guesthouse_payload

from tests.helpers import make_participant
from tests.test_investigator_playtest_ui import _start_investigator_ui_session
from tests.test_keeper_dashboard_ui import (
    _advance_keeper_dashboard_session,
    _start_keeper_dashboard_session,
)
from tests.test_session_import import KEEPER_ID, _get_snapshot
from tests.test_playtest_session_index_ui import _start_grouped_snapshot_session


class _FakeLocalLLMService:
    def __init__(self) -> None:
        self.requests = []
        self.enabled = True

    def generate_assistant(self, request):
        self.requests.append(request)
        source_object = request.context.get("source_object") or {}
        object_kind = source_object.get("object_kind")
        object_id = source_object.get("object_id")
        object_label = source_object.get("object_label")
        knowledge_source = request.context.get("source") or {}
        draft_kind = {
            "note_draft": "prompt_note_draft",
            "draft_review_note_draft": "draft_review_note_draft",
            "source_summary": "knowledge_summary_note_draft",
            "follow_up_questions": "knowledge_follow_up_note_draft",
        }.get(request.task_key)
        suggested_target = {
            "note_draft": "prompt_note",
            "draft_review_note_draft": "draft_review_editor_notes",
            "source_summary": "knowledge_work_note",
            "follow_up_questions": "knowledge_work_note",
        }.get(request.task_key)
        source_context_label = {
            "note_draft": "基于当前 keeper workspace 摘要与待处理 prompts。",
            "draft_review_note_draft": "基于当前 keeper workspace 摘要与待审草稿概览。",
            "source_summary": "基于当前资料摘要与预览。",
            "follow_up_questions": "基于当前资料摘要与预览。",
        }.get(request.task_key)
        if object_kind == "prompt" and object_id and object_label:
            source_context_label = f"基于当前 prompt：{object_label}（{object_id}）。"
        if object_kind == "draft" and object_id and object_label:
            source_context_label = f"基于当前待审草稿：{object_label}（{object_id}）。"
        if request.workspace_key == "knowledge_detail":
            source_id = knowledge_source.get("source_id") or "source"
            source_label = knowledge_source.get("source_title_zh") or source_id
            source_context_label = f"基于当前资料：{source_label}（{source_id}）的摘要与预览。"
        return LocalLLMAssistantResult(
            status="success",
            workspace_key=request.workspace_key,
            task_key=request.task_key,
            task_label=request.task_label,
            provider_name="stub-local",
            model="stub-model",
            assistant=LocalLLMAssistantPayload(
                title=f"{request.task_label} 结果",
                summary="这是非权威辅助输出。",
                bullets=["关键点一", "关键点二"],
                suggested_questions=["后续还要确认什么？"],
                draft_text=(
                    "可先把当前资料的关键点整理成工作备注。"
                    if request.task_key == "source_summary"
                    else (
                        "- 地下储物间和登记簿之间还有什么缺口？\n- 这条线索是否能指向失踪住客？"
                        if request.task_key == "follow_up_questions"
                        else "这是一段可继续编辑的草稿。"
                    )
                ),
                draft_kind=draft_kind,
                suggested_target=suggested_target,
                source_context_label=source_context_label,
                safety_notes=["不会直接改写 authoritative state。"],
            ),
        )


def _register_text_source(
    client: TestClient,
    *,
    source_id: str,
    source_title_zh: str,
    content: str,
    source_kind: str = "rulebook",
) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": "plain_text",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
        },
    )
    assert register_response.status_code == 201
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert ingest_response.status_code == 200


def test_web_app_sessions_and_group_pages_unify_navigation_shell(
    client: TestClient,
) -> None:
    group_name = "旅店线压力测试"
    grouped_session_id = _start_grouped_snapshot_session(
        client,
        playtest_group=group_name,
    )
    ungrouped_session_id = _start_grouped_snapshot_session(
        client,
        playtest_group=None,
    )

    response = client.get("/app/sessions")

    assert response.status_code == 200
    html = response.text
    assert "Web GUI MVP" in html
    assert "Session Workspace Index" in html
    assert "Sessions" in html
    assert "Keeper" in html
    assert "Investigator" in html
    assert "Knowledge" in html
    assert "Recap / Review" in html
    assert grouped_session_id in html
    assert ungrouped_session_id in html
    assert f'/app/sessions/{grouped_session_id}"' in html
    assert f'/app/sessions/{grouped_session_id}/keeper"' in html
    assert f'/app/sessions/{grouped_session_id}/recap"' in html
    assert f'href="/app/groups/{quote(group_name)}"' in html
    assert 'href="/app/setup"' in html

    group_response = client.get(f"/app/groups/{quote(group_name)}")

    assert group_response.status_code == 200
    group_html = group_response.text
    assert f"分组：{group_name}" in group_html
    assert grouped_session_id in group_html
    assert ungrouped_session_id not in group_html
    assert f'href="/app/setup?playtest_group={quote(group_name)}"' in group_html


def test_web_app_setup_flow_creates_session_inside_app_shell(
    client: TestClient,
) -> None:
    response = client.get("/app/setup?playtest_group=%E7%AC%AC%E4%BA%8C%E9%98%B6%E6%AE%B5")

    assert response.status_code == 200
    html = response.text
    assert "在 App Shell 内创建新局" in html
    assert 'name="keeper_name"' in html
    assert 'name="scenario_template"' in html
    assert 'name="investigator_1_name"' in html
    assert "第二阶段" in html
    assert 'action="/app/setup"' in html

    create_response = client.post(
        "/app/setup",
        data={
            "keeper_name": "KP",
            "playtest_group": "第二阶段",
            "scenario_template": "whispering_guesthouse",
            "investigator_1_name": "林舟",
            "investigator_2_name": "周岚",
        },
        follow_redirects=False,
    )

    assert create_response.status_code == 303
    location = create_response.headers["location"]
    assert location.startswith("/app/sessions/")

    overview_response = client.get(location)
    assert overview_response.status_code == 200
    overview_html = overview_response.text
    assert "Session Overview" in overview_html
    assert "雾港旅店的低语" in overview_html
    assert "第二阶段" in overview_html


def test_web_app_keeper_assistant_block_defaults_to_disabled_without_breaking_workspace(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)

    response = client.get(f"/app/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "Keeper Assistant" in html
    assert "Local LLM 未启用" in html
    assert "不会直接修改 authoritative state" in html


def test_web_app_keeper_workspace_surfaces_pending_ops_and_legacy_handoffs(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)

    response = client.get(f"/app/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "Keeper Workspace" in html
    assert "主操作区" in html
    assert "Lifecycle" in html
    assert "Combat Control" in html
    assert "待处理事项" in html
    assert "KP Prompts" in html
    assert "Draft Review" in html
    assert "控场摘要" in html
    assert "战斗与伤势" in html
    assert "规则与知识辅助" in html
    assert f'action="/app/sessions/{session_id}/keeper/lifecycle"' in html
    assert "只有进行中的会话才能开始或推进战斗顺序。" in html
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in html
    assert "KP 草稿：若调查员继续追问秦老板，应准备对话压力。" in html
    assert f'action="/app/sessions/{session_id}/keeper/prompts/' in html
    assert f'action="/app/sessions/{session_id}/keeper/prompts/' in html
    assert f'action="/app/sessions/{session_id}/draft-actions/' in html
    assert "为这条 Prompt 生成备注草稿" in html
    assert "为这条草稿生成审阅说明" in html
    assert 'name="note"' in html
    assert 'name="editor_notes"' in html
    assert f'/playtest/sessions/{session_id}/keeper#prompt-targets"' in html
    assert f'/playtest/sessions/{session_id}/keeper#draft-review-targets"' in html
    assert f'/playtest/sessions/{session_id}/keeper#combat-flow"' in html
    assert f'/playtest/sessions/{session_id}/keeper#live-control"' in html


def test_web_app_prompt_card_shows_pre_generation_local_context_preview(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)

    response = client.get(f"/app/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "本次生成将使用的局部上下文摘要" in html
    assert "当前 Prompt：" in html
    assert "当前状态 / 类别：" in html
    assert "最近 note：" in html
    assert "最近处理摘要：" in html
    assert "本次草稿将基于当前 prompt 与最近处理上下文生成，不会直接执行任何动作。" in html
    assert "本次已生成的来源回显" not in html
    assert "当前尚未带入。若采纳，将带入Prompt 备注" not in html


def test_web_app_draft_card_shows_pre_generation_local_context_preview(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)

    response = client.get(f"/app/sessions/{session_id}/keeper")

    assert response.status_code == 200
    html = response.text
    assert "当前草稿：" in html
    assert "当前 review 状态：" in html
    assert "最近 editor note：" in html
    assert "最近 review 摘要：" in html
    assert "本次草稿将基于当前 draft 与最近审阅上下文生成，不会直接执行任何动作。" in html
    assert "本次已生成的来源回显" not in html
    assert "当前尚未带入。若采纳，将带入草稿审阅说明" not in html


def test_web_app_keeper_assistant_uses_keeper_context_without_writing_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/keeper/assistant",
        data={"assistant_task": "note_draft"},
    )

    assert response.status_code == 200
    html = response.text
    assert "主持人备注草稿 结果" in html
    assert "这是非权威辅助输出。" in html
    assert "不会直接改写 authoritative state。" in html
    assert "草稿类型：Prompt 备注草稿" in html
    assert "推荐带入：Prompt 备注" in html
    assert "来源语境：基于当前 keeper workspace 摘要与待处理 prompts。" in html
    assert 'id="keeper-assistant-draft-source"' in html
    assert 'data-adopt-source="keeper-assistant-draft-source"' in html
    assert 'data-adopt-target="prompt-note-' in html
    assert 'data-adopt-status="prompt-note-status-' in html
    assert 'data-adopt-status-text="已带入 Prompt 备注草稿。来源：基于当前 keeper workspace 摘要与待处理 prompts。 当前仍需 Keeper 人工编辑并提交。"' in html
    assert '>带入当前 Prompt 备注框</button>' in html
    assert "当前可采纳：Prompt 备注草稿。来源：基于当前 keeper workspace 摘要与待处理 prompts。 目标：当前 Prompt 备注框。" in html
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.workspace_key == "keeper_workspace"
    assert request.task_key == "note_draft"
    assert request.context["active_prompts"]
    assert "KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。" in str(
        request.context["active_prompts"][0]["prompt_text"]
    )
    serialized_context = str(request.context)
    assert "private_notes" not in serialized_context
    assert "secret_state_refs" not in serialized_context
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_keeper_assistant_review_note_adoption_targets_editor_notes(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/keeper/assistant",
        data={"assistant_task": "draft_review_note_draft"},
    )

    assert response.status_code == 200
    html = response.text
    assert "草稿审阅说明草稿 结果" in html
    assert "草稿类型：草稿审阅说明草稿" in html
    assert "推荐带入：草稿审阅说明" in html
    assert "来源语境：基于当前 keeper workspace 摘要与待审草稿概览。" in html
    assert 'data-adopt-source="keeper-assistant-draft-source"' in html
    assert 'data-adopt-target="draft-review-note-' in html
    assert '>带入当前草稿审阅说明框</button>' in html
    assert "当前可采纳：草稿审阅说明草稿。来源：基于当前 keeper workspace 摘要与待审草稿概览。 目标：当前草稿审阅说明框。" in html
    assert 'type="button"' in html
    assert 'data-adopt-status="draft-review-status-' in html
    assert 'data-adopt-status-text="已带入 草稿审阅说明草稿。来源：基于当前 keeper workspace 摘要与待审草稿概览。 当前仍需 Keeper 人工编辑并提交。"' in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_prompt_card_can_generate_object_scoped_assistant_draft(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    _, keeper_view, _, _ = client.app.state.session_service.get_keeper_workspace(session_id)
    prompt = keeper_view.keeper_workflow.active_prompts[0]
    prompt_id = prompt.prompt_id
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/keeper/prompts/{prompt_id}/assistant",
    )

    assert response.status_code == 200
    html = response.text
    assert "当前对象：单条 Prompt" in html
    assert f"对象标识：{prompt_id}" in html
    assert "来源语境：基于当前 prompt：" in html
    assert "及最近处理上下文。" in html
    assert "局部上下文：" in html
    assert "本次生成将使用的局部上下文摘要" in html
    assert "本次已生成的来源回显" in html
    assert "草稿归属：当前 Prompt" in html
    assert "实际参考的局部字段：当前状态 / 类别" in html
    assert "推荐带入目标：Prompt 备注" in html
    assert f'id="prompt-flow-status-{prompt_id}"' in html
    assert "当前尚未带入。若采纳，将带入Prompt 备注，之后仍需 Keeper 人工编辑并提交。" in html
    assert prompt.prompt_text[:12] in html
    assert f'data-adopt-target="prompt-note-{prompt_id}"' in html
    assert 'data-adopt-target="draft-review-note-' not in html
    assert f'data-adopt-flow-status="prompt-flow-status-{prompt_id}"' in html
    assert 'data-adopt-flow-status-text="该草稿来自当前 Prompt 的 assistant 生成。已带入：Prompt 备注框。当前仍待 Keeper 人工编辑并提交。"' in html
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.context["source_object"]["object_kind"] == "prompt"
    assert request.context["source_object"]["object_id"] == prompt_id
    assert request.context["prompt_local_context"]["context_summary"]
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_draft_card_can_generate_object_scoped_assistant_draft(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    _, keeper_view, _, _ = client.app.state.session_service.get_keeper_workspace(session_id)
    draft = keeper_view.visible_draft_actions[0]
    draft_id = draft.draft_id
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/draft-actions/{draft_id}/assistant",
    )

    assert response.status_code == 200
    html = response.text
    assert "当前对象：单条待审草稿" in html
    assert f"对象标识：{draft_id}" in html
    assert "来源语境：基于当前待审草稿：" in html
    assert "及最近审阅上下文。" in html
    assert "局部上下文：" in html
    assert "本次生成将使用的局部上下文摘要" in html
    assert "本次已生成的来源回显" in html
    assert "草稿归属：当前待审草稿" in html
    assert "推荐带入目标：草稿审阅说明" in html
    assert "最近 editor note：" in html
    assert f'id="draft-flow-status-{draft_id}"' in html
    assert "当前尚未带入。若采纳，将带入草稿审阅说明，之后仍需 Keeper 人工编辑并提交。" in html
    assert draft.draft_text[:12] in html
    assert f'data-adopt-target="draft-review-note-{draft_id}"' in html
    assert 'data-adopt-target="prompt-note-' not in html
    assert f'data-adopt-flow-status="draft-flow-status-{draft_id}"' in html
    assert 'data-adopt-flow-status-text="该草稿来自当前待审草稿的 assistant 生成。已带入：草稿审阅说明框。当前仍待 Keeper 人工编辑并提交。"' in html
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.context["source_object"]["object_kind"] == "draft"
    assert request.context["source_object"]["object_id"] == draft_id
    assert request.context["draft_local_context"]["current_review_status"] == "pending"
    assert "当前 review 状态" in request.context["draft_local_context"]["context_summary"]
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_keeper_prompt_submit_requires_manual_post_after_adoption_markup(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    _, keeper_view, _, _ = client.app.state.session_service.get_keeper_workspace(session_id)
    prompt_id = keeper_view.keeper_workflow.active_prompts[0].prompt_id

    response = client.post(
        f"/app/sessions/{session_id}/keeper/prompts/{prompt_id}/status",
        data={
            "operator_id": KEEPER_ID,
            "status": "acknowledged",
            "note": "先记入主持人备注，再观察调查员是否继续追问。",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "Keeper Prompt 已更新" in html
    assert "先记入主持人备注，再观察调查员是否继续追问。" in html
    assert "当前 Prompt 已人工提交，对象卡已恢复默认状态，不再显示上一轮待提交提示。" in html
    assert "本次已生成的来源回显" not in html
    assert "当前尚未带入。若采纳，将带入Prompt 备注" not in html
    assert "已带入：Prompt 备注框" not in html


def test_web_app_keeper_draft_review_submit_requires_manual_post_after_adoption_markup(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    _, keeper_view, _, _ = client.app.state.session_service.get_keeper_workspace(session_id)
    draft_id = keeper_view.visible_draft_actions[0].draft_id

    response = client.post(
        f"/app/sessions/{session_id}/draft-actions/{draft_id}/review",
        data={
            "reviewer_id": KEEPER_ID,
            "decision": "approve",
            "editor_notes": "采用这条草稿，但仍由 Keeper 手工确认后提交。",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "Draft Review 已提交" in html
    assert "采用这条草稿，但仍由 Keeper 手工确认后提交。" in html
    assert "当前草稿审阅已人工提交，对象卡已恢复默认状态，不再显示上一轮待提交提示。" in html
    assert "本次已生成的来源回显" not in html
    assert "当前尚未带入。若采纳，将带入草稿审阅说明" not in html
    assert "已带入：草稿审阅说明框" not in html


def test_web_app_investigator_workspace_preserves_secret_boundary_and_action_groups(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(
        client,
        participants=[
            make_participant("investigator-1", "林舟"),
            make_participant("investigator-2", "周岚"),
        ],
    )

    response = client.get(f"/app/sessions/{session_id}/investigator/investigator-1")

    assert response.status_code == 200
    html = response.text
    assert "调查员工作区" in html
    assert "常驻状态" in html
    assert "可见线索" in html
    assert "行动与检定" in html
    assert "主要动作" in html
    assert "战斗摘要" in html
    assert "私有备注" in html
    assert f'action="/app/sessions/{session_id}/investigator/investigator-1/skill-check"' in html
    assert f'action="/app/sessions/{session_id}/investigator/investigator-1/attribute-check"' in html
    assert f'action="/app/sessions/{session_id}/investigator/investigator-1/san-check"' in html
    assert f'action="/app/sessions/{session_id}/investigator/investigator-1/melee-attack"' in html
    assert f'action="/app/sessions/{session_id}/investigator/investigator-1/ranged-attack"' in html
    assert "当前角色没有急救或医学技能可用于紧急急救。" in html
    assert "林舟 的私人笔记" in html
    assert "周岚 的私人笔记" not in html
    assert "会话生命周期" not in html
    assert "实时控场" not in html
    assert "/keeper/lifecycle" not in html
    assert f'/playtest/sessions/{session_id}/investigator/investigator-1"' in html


def test_web_app_keeper_actions_update_lifecycle_combat_and_wound_follow_up(
    client: TestClient,
    monkeypatch,
) -> None:
    target = make_participant("investigator-1", "林舟")
    attacker = make_participant("investigator-2", "周岚")
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": KEEPER_ID,
            "scenario": whispering_guesthouse_payload(),
            "participants": [target, attacker],
        },
    )
    assert response.status_code == 201
    session_id = response.json()["session_id"]

    activate_response = client.post(
        f"/app/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200
    activate_html = activate_response.text
    assert "会话生命周期已更新" in activate_html
    assert "会话状态已切换为进行中" in activate_html

    combat_response = client.post(
        f"/app/sessions/{session_id}/keeper/combat/start",
        data={"operator_id": KEEPER_ID, "starting_actor_id": "investigator-2"},
    )
    assert combat_response.status_code == 200
    combat_html = combat_response.text
    assert "战斗流程已更新" in combat_html
    assert "已建立战斗顺序" in combat_html
    assert "推进到下一位行动者" in combat_html

    fixed_rolls = iter(
        [
            D100Roll(
                unit_die=3,
                tens_dice=[2],
                selected_tens=2,
                total=23,
                target=55,
                outcome=RollOutcome.HARD_SUCCESS,
            ),
            D100Roll(
                unit_die=2,
                tens_dice=[6],
                selected_tens=6,
                total=62,
                target=40,
                outcome=RollOutcome.FAILURE,
            ),
        ]
    )
    client.app.state.session_service.dice_execution_backend = LocalDiceExecutionBackend(
        roller=lambda target, *, seed=None, bonus_dice=0, penalty_dice=0: next(fixed_rolls)
    )
    monkeypatch.setattr(
        session_service_module,
        "roll_damage_expression",
        lambda expression, *, db_expression=None, seed=None: 11,
    )

    attack_response = client.post(
        f"/app/sessions/{session_id}/investigator/investigator-2/melee-attack",
        data={
            "melee_target_actor_id": "investigator-1",
            "attack_label": "斗殴",
            "attack_target_value": "55",
            "defense_mode": "dodge",
            "defense_label": "闪避",
            "defense_target_value": "40",
        },
    )
    assert attack_response.status_code == 200

    damage_response = client.post(
        f"/app/sessions/{session_id}/investigator/investigator-2/damage-resolution",
        data={
            "damage_target_actor_id": "investigator-1",
            "damage_expression": "1d6+1",
            "damage_bonus_expression": "",
            "armor_value": "0",
        },
    )
    assert damage_response.status_code == 200

    before_keeper_response = client.get(f"/app/sessions/{session_id}/keeper")
    assert before_keeper_response.status_code == 200
    before_keeper_html = before_keeper_response.text
    assert "保留抢救窗口" in before_keeper_html
    assert "确认死亡" in before_keeper_html

    resolve_response = client.post(
        f"/app/sessions/{session_id}/keeper/wounds/investigator-1/resolve",
        data={
            "operator_id": KEEPER_ID,
            "resolution": "confirm_death",
        },
    )
    assert resolve_response.status_code == 200
    resolve_html = resolve_response.text
    assert "伤势后续已裁定" in resolve_html
    assert "已确认林舟死亡" in resolve_html

    snapshot = _get_snapshot(client, session_id)
    target_state = snapshot["character_states"]["investigator-1"]
    assert target_state["death_confirmed"] is True
    assert target_state["rescue_window_open"] is False


def test_web_app_investigator_actions_run_inside_shell(
    client: TestClient,
    monkeypatch,
) -> None:
    target = make_participant("investigator-1", "林舟")
    healer = make_participant("investigator-2", "周岚")
    healer["character"]["skills"]["急救"] = 60
    session_id = _start_investigator_ui_session(client, participants=[target, healer])

    skill_response = client.post(
        f"/app/sessions/{session_id}/investigator/investigator-1/skill-check",
        data={"skill_name": "图书馆使用", "dice_modifier": "normal"},
    )
    assert skill_response.status_code == 200
    skill_html = skill_response.text
    assert "最近一次技能检定" in skill_html
    assert "已完成技能检定" in skill_html

    fixed_rolls = iter(
        [
            D100Roll(
                unit_die=3,
                tens_dice=[2],
                selected_tens=2,
                total=23,
                target=55,
                outcome=RollOutcome.HARD_SUCCESS,
            ),
            D100Roll(
                unit_die=2,
                tens_dice=[6],
                selected_tens=6,
                total=62,
                target=40,
                outcome=RollOutcome.FAILURE,
            ),
            D100Roll(
                unit_die=5,
                tens_dice=[2],
                selected_tens=2,
                total=25,
                target=60,
                outcome=RollOutcome.SUCCESS,
            ),
        ]
    )
    client.app.state.session_service.dice_execution_backend = LocalDiceExecutionBackend(
        roller=lambda target, *, seed=None, bonus_dice=0, penalty_dice=0: next(fixed_rolls)
    )
    monkeypatch.setattr(
        session_service_module,
        "roll_damage_expression",
        lambda expression, *, db_expression=None, seed=None: 11,
    )

    attack_response = client.post(
        f"/app/sessions/{session_id}/investigator/investigator-2/melee-attack",
        data={
            "melee_target_actor_id": "investigator-1",
            "attack_label": "斗殴",
            "attack_target_value": "55",
            "defense_mode": "dodge",
            "defense_label": "闪避",
            "defense_target_value": "40",
        },
    )
    assert attack_response.status_code == 200
    attack_html = attack_response.text
    assert "最近一次近战攻击" in attack_html
    assert "已完成近战攻击判定" in attack_html

    damage_response = client.post(
        f"/app/sessions/{session_id}/investigator/investigator-2/damage-resolution",
        data={
            "damage_target_actor_id": "investigator-1",
            "damage_expression": "1d6+1",
            "damage_bonus_expression": "",
            "armor_value": "0",
        },
    )
    assert damage_response.status_code == 200
    damage_html = damage_response.text
    assert "最近一次伤害结算" in damage_html
    assert "已完成伤害结算，目标 HP 已更新" in damage_html

    first_aid_response = client.post(
        f"/app/sessions/{session_id}/investigator/investigator-2/first-aid",
        data={
            "first_aid_target_actor_id": "investigator-1",
            "first_aid_skill_name": "急救",
        },
    )
    assert first_aid_response.status_code == 200
    first_aid_html = first_aid_response.text
    assert "最近一次急救" in first_aid_html
    assert "已完成紧急急救检定" in first_aid_html
    assert "状态：濒死（仍可救助）" in first_aid_html
    assert "昏迷但稳定" in first_aid_html

    investigator_response = client.get(
        f"/app/sessions/{session_id}/investigator/investigator-1"
    )
    assert investigator_response.status_code == 200
    investigator_html = investigator_response.text
    assert "已稳定" in investigator_html
    assert "短时可救" not in investigator_html


def test_web_app_knowledge_workspace_and_detail_keep_session_backlink(
    client: TestClient,
) -> None:
    session_id = _start_grouped_snapshot_session(client, playtest_group="知识回链")
    _register_text_source(
        client,
        source_id="guesthouse-rules",
        source_title_zh="旅店规则摘录",
        content="侦查检定用于发现地板缝里的隐藏纸条。",
    )

    response = client.get(f"/app/knowledge?session_id={session_id}")

    assert response.status_code == 200
    html = response.text
    assert "准备资料 / 模板卡 / 扫描" in html
    assert "旅店规则摘录" in html
    assert "登记或扫描资料" in html
    assert f'href="/app/sessions/{session_id}"' in html
    assert f'/app/knowledge/guesthouse-rules?session_id={session_id}"' in html
    assert 'href="/playtest/knowledge"' in html

    detail_response = client.get(
        f"/app/knowledge/guesthouse-rules?session_id={session_id}"
    )

    assert detail_response.status_code == 200
    detail_html = detail_response.text
    assert "资料摘要" in detail_html
    assert "内容预览" in detail_html
    assert "侦查检定用于发现地板缝里的隐藏纸条。" in detail_html
    assert "Knowledge Assistant" in detail_html
    assert "Local LLM 未启用" in detail_html
    assert 'action="/app/knowledge/guesthouse-rules/working-note"' in detail_html
    assert 'name="working_note"' in detail_html
    assert f'href="/app/knowledge?session_id={session_id}"' in detail_html
    assert 'href="/playtest/knowledge/guesthouse-rules"' in detail_html


def test_web_app_knowledge_assistant_uses_source_preview_without_session_private_state(
    client: TestClient,
) -> None:
    session_id = _start_grouped_snapshot_session(client, playtest_group="知识助手")
    _register_text_source(
        client,
        source_id="assistant-source",
        source_title_zh="助手测试资料",
        content="旧账册暗示地下储物间与消失的住客登记存在关联。",
    )
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_source = client.app.state.knowledge_service.get_source("assistant-source").model_dump(
        mode="json"
    )

    response = client.post(
        "/app/knowledge/assistant-source/assistant",
        data={
            "assistant_task": "follow_up_questions",
            "session_id": session_id,
            "working_note": "已有手工假说",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "可追问问题 结果" in html
    assert "这是非权威辅助输出。" in html
    assert "当前可采纳草稿" in html
    assert "草稿类型：追问问题草稿" in html
    assert "推荐带入：知识工作备注" in html
    assert "当前对象：当前资料" in html
    assert "对象标识：assistant-source" in html
    assert "来源语境：基于当前资料：助手测试资料（assistant-source）的摘要与预览。" in html
    assert "局部上下文：当前资料摘要、预览片段与已展示提取结果，不含未展示的 session 私密信息。" in html
    assert "带入当前页工作备注框" in html
    assert 'data-adopt-target="knowledge-work-note-assistant-source"' in html
    assert (
        'data-adopt-status-text="已带入 追问问题草稿。来源：基于当前资料：助手测试资料（assistant-source）的摘要与预览。 当前仍需人工编辑并提交。"'
        in html
    )
    assert (
        'data-adopt-flow-status-text="该草稿来自当前资料页的 assistant 生成。已带入：当前页工作备注框。当前仍待人工编辑并提交。"'
        in html
    )
    assert "当前尚未带入。若采纳，将带入当前页工作备注框，之后仍需人工编辑并提交。" in html
    assert "已有手工假说" in html
    after_source = client.app.state.knowledge_service.get_source("assistant-source").model_dump(
        mode="json"
    )
    assert before_source == after_source
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.workspace_key == "knowledge_detail"
    assert request.task_key == "follow_up_questions"
    assert "source" in request.context
    assert "preview_chunks" in request.context
    serialized_context = str(request.context)
    assert "participants" not in serialized_context
    assert "private_notes" not in serialized_context
    assert "session_id':" not in serialized_context


def test_web_app_knowledge_working_note_submit_stays_non_authoritative(
    client: TestClient,
) -> None:
    _register_text_source(
        client,
        source_id="note-source",
        source_title_zh="工作备注资料",
        content="旧账册里反复出现 204 房、地窖和搬运时间的对应关系。",
    )
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_source = client.app.state.knowledge_service.get_source("note-source").model_dump(
        mode="json"
    )

    response = client.post(
        "/app/knowledge/note-source/working-note",
        data={
            "working_note": "假说：204 房的住客登记和地窖搬运时间存在对应关系。",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "当前页工作备注已人工确认" in html
    assert "不会写入 knowledge 主状态" in html
    assert "假说：204 房的住客登记和地窖搬运时间存在对应关系。" in html
    assert 'name="working_note"' in html
    assert "当前可采纳草稿" not in html
    assert "带入当前知识工作备注框" not in html
    after_source = client.app.state.knowledge_service.get_source("note-source").model_dump(
        mode="json"
    )
    assert before_source == after_source


def test_web_app_recap_page_joins_timeline_and_review_shell(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)

    response = client.get(f"/app/sessions/{session_id}/recap")

    assert response.status_code == 200
    html = response.text
    assert "Recap / Review" in html
    assert "最近时间线" in html
    assert "Audit / Review" in html
    assert "Closeout 摘要" in html
    assert "我趁老板转身时抽出柜台后的旧图纸并溜进账房。" in html
    assert f'/app/sessions/{session_id}/keeper"' in html
    assert f'/playtest/sessions/{session_id}/recap"' in html


def test_web_app_recap_assistant_generates_draft_without_mutating_state(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/recap/assistant",
        data={"assistant_task": "recap_draft"},
    )

    assert response.status_code == 200
    html = response.text
    assert "Recap Assistant" in html
    assert "本局 recap 草稿 结果" in html
    assert "这是非权威辅助输出。" in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.workspace_key == "session_recap"
    serialized_context = str(request.context)
    assert "private_notes" not in serialized_context
    assert "own_private_state" not in serialized_context
