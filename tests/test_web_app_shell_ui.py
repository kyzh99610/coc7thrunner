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
            "scene_framing": "scene_framing_note_draft",
            "clue_beat": "clue_beat_note_draft",
            "npc_pressure": "npc_pressure_note_draft",
        }.get(request.task_key)
        suggested_target = {
            "note_draft": "prompt_note",
            "draft_review_note_draft": "draft_review_editor_notes",
            "source_summary": "knowledge_work_note",
            "follow_up_questions": "knowledge_work_note",
            "scene_framing": "narrative_work_note",
            "clue_beat": "narrative_work_note",
            "npc_pressure": "narrative_work_note",
        }.get(request.task_key)
        source_context_label = {
            "note_draft": "基于当前 keeper workspace 摘要与待处理 prompts。",
            "draft_review_note_draft": "基于当前 keeper workspace 摘要与待审草稿概览。",
            "source_summary": "基于当前资料摘要与预览。",
            "follow_up_questions": "基于当前资料摘要与预览。",
            "scene_framing": "基于当前 keeper workspace：旅店账房 / 核对账房记录。",
            "clue_beat": "基于当前 keeper workspace：旅店账房 / 核对账房记录。",
            "npc_pressure": "基于当前 keeper workspace：旅店账房 / 核对账房记录。",
        }.get(request.task_key)
        if object_kind == "prompt" and object_id and object_label:
            source_context_label = f"基于当前 prompt：{object_label}（{object_id}）。"
        if object_kind == "draft" and object_id and object_label:
            source_context_label = f"基于当前待审草稿：{object_label}（{object_id}）。"
        if request.workspace_key == "knowledge_detail":
            source_id = knowledge_source.get("source_id") or "source"
            source_label = knowledge_source.get("source_title_zh") or source_id
            source_context_label = f"基于当前资料：{source_label}（{source_id}）的摘要与预览。"
        if request.workspace_key == "experimental_ai_kp_demo":
            source_context_label = "基于当前 keeper-side compressed context 与近期事件摘要。"
        if request.workspace_key == "experimental_ai_investigator_demo":
            viewer = request.context.get("viewer") or {}
            viewer_label = viewer.get("display_name") or viewer.get("actor_id") or "调查员"
            source_context_label = f"基于 {viewer_label} 的可见状态摘要。"
        title = f"{request.task_label} 结果"
        summary = "这是非权威辅助输出。"
        draft_text = (
            "可先把当前资料的关键点整理成工作备注。"
            if request.task_key == "source_summary"
            else (
                "- 地下储物间和登记簿之间还有什么缺口？\n- 这条线索是否能指向失踪住客？"
                if request.task_key == "follow_up_questions"
                else (
                    "开场可先把账房里的旧账册、潮气和老板的目光压力压出来。"
                    if request.task_key == "scene_framing"
                    else (
                        "建议下一拍让缺页编号通过账册边角或店员反应被看见。"
                        if request.task_key == "clue_beat"
                        else (
                            "秦老板会先压低声音否认，再用催促离店制造时间压力。"
                            if request.task_key == "npc_pressure"
                            else "这是一段可继续编辑的草稿。"
                        )
                    )
                )
            )
        )
        bullets = ["关键点一", "关键点二"]
        suggested_questions = ["后续还要确认什么？"]
        safety_notes = ["不会直接改写 authoritative state。"]
        if request.workspace_key == "experimental_ai_kp_demo":
            title = "AI KP 剧情支架提案"
            summary = "这是 experimental / non-authoritative 的 AI KP 候选叙事输出。"
            bullets = ["先立起账房压迫感。", "让秦老板的反应制造下一拍压力。"]
            suggested_questions = ["是否先让调查员听见二楼动静？"]
            draft_text = "KP 可先用潮气、旧账册和老板的短暂失态开场，再把压力推向缺页登记。"
            safety_notes = ["不会自动推进剧情。", "不会写入 authoritative session state。"]
        if request.workspace_key == "experimental_ai_investigator_demo":
            title = "AI Investigator 行动提案"
            summary = "这是 experimental / non-authoritative 的调查员行动提案。"
            bullets = ["先确认账册缺页编号。", "再试探老板是否回避 204 房记录。"]
            suggested_questions = ["204 房登记是否和地窖搬运时间有关？"]
            draft_text = "调查员会先盯住账册缺页和住客编号，再顺势追问老板为何回避 204 房。"
            safety_notes = ["只基于可见信息。", "不会自动执行检定或推进状态。"]
        if request.workspace_key == "experimental_ai_keeper_continuity_draft":
            title = "Keeper continuity bridge 草稿"
            summary = "这是 experimental / non-authoritative 的 keeper continuity 草稿。"
            bullets = ["保留老板回避和二楼脚步声造成的下一轮压力。"]
            suggested_questions = ["下一轮是否继续把压力推向 204 房和二楼动静？"]
            draft_text = (
                "Keeper 暂定保留账册缺页、老板回避和二楼脚步声作为下一轮内部 continuity，"
                "并把压力从账房转向 204 房与楼上传来的异常动静。"
            )
            safety_notes = ["仅用于当前页 continuity bridge 起草。", "不会自动推进剧情或写入 authoritative state。"]
        if request.workspace_key == "experimental_ai_visible_continuity_draft":
            title = "Visible continuity bridge 草稿"
            summary = "这是 experimental / non-authoritative 的 visible continuity 草稿。"
            bullets = ["调查员只确认到账册缺页、老板回避和二楼脚步声。"]
            suggested_questions = ["下一轮是否继续公开追问 204 房记录？"]
            draft_text = (
                "调查员目前只确认了账册缺页、老板回避和二楼脚步声，"
                "下一轮可继续沿 204 房登记与楼上动静公开追问。"
            )
            safety_notes = ["只基于公开可见信息起草。", "不会自动推进剧情或写入 authoritative state。"]
        return LocalLLMAssistantResult(
            status="success",
            workspace_key=request.workspace_key,
            task_key=request.task_key,
            task_label=request.task_label,
            provider_name="stub-local",
            model="stub-model",
            assistant=LocalLLMAssistantPayload(
                title=title,
                summary=summary,
                bullets=bullets,
                suggested_questions=suggested_questions,
                draft_text=draft_text,
                draft_kind=draft_kind,
                suggested_target=suggested_target,
                source_context_label=source_context_label,
                safety_notes=safety_notes,
            ),
        )


def _make_experimental_result(
    *,
    workspace_key: str,
    task_label: str,
    title: str,
    summary: str,
) -> LocalLLMAssistantResult:
    return LocalLLMAssistantResult(
        status="success",
        workspace_key=workspace_key,
        task_key="demo_loop",
        task_label=task_label,
        provider_name="stub-local",
        model="stub-model",
        assistant=LocalLLMAssistantPayload(
            title=title,
            summary=summary,
            bullets=["要点一", "要点二"],
            suggested_questions=["下一步还要确认什么？"],
            draft_text="这是一段实验输出草稿。",
            safety_notes=["不会直接修改 authoritative state。"],
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
    assert "Keeper Context Pack" in html
    assert "Compact Recap / 压缩工作摘要" in html
    assert "当前局势一句话" in html
    assert 'id="keeper-context-pack"' in html
    assert "AI-KP Narrative Scaffolding" in html
    assert "Keeper Assistant" in html
    assert f'href="/app/sessions/{session_id}/experimental-ai-demo"' in html
    assert "Local LLM 未启用" in html
    assert "不会直接修改 authoritative state" in html
    assert 'action="/app/sessions/' in html
    assert 'name="narrative_note"' in html


def test_web_app_experimental_ai_demo_page_loads_without_breaking_keeper_shell_when_llm_disabled(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)

    response = client.get(f"/app/sessions/{session_id}/experimental-ai-demo")

    assert response.status_code == 200
    html = response.text
    assert "AI KP + AI Investigator Demo Harness" in html
    assert "Experimental / Non-authoritative" in html
    assert "运行最小实验回合" in html
    assert "AI KP 输入：Compressed Context" in html
    assert "AI Investigator 输入摘要" in html
    assert "AI KP Demo Output" in html
    assert "AI Investigator Demo Output" in html
    assert "Local LLM 未启用" in html
    assert "不会自动写入主状态" in html
    assert "运行 self-play 预演链" in html
    assert 'action="/app/sessions/' in html
    assert 'name="keeper_turn_outcome_note"' not in html
    assert 'name="visible_turn_outcome_note"' not in html
    assert "当前页实验评估" not in html


def test_web_app_experimental_ai_demo_launcher_entry_redirects_to_latest_session_demo(
    client: TestClient,
) -> None:
    latest_session_id = _start_keeper_dashboard_session(client)
    older_session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, latest_session_id)

    response = client.get("/app/experimental-ai-demo", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/app/sessions/{latest_session_id}/experimental-ai-demo"
    )
    assert response.headers["location"] != (
        f"/app/sessions/{older_session_id}/experimental-ai-demo"
    )


def test_web_app_experimental_ai_demo_launcher_entry_falls_back_to_sessions_index_without_session(
    client: TestClient,
) -> None:
    response = client.get("/app/experimental-ai-demo", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/app/sessions"


def test_keeper_compressed_context_builder_stays_short_and_secret_safe(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    service = client.app.state.session_service
    session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
        runtime_assistance=runtime_assistance,
        narrative_work_note="先把账房里的旧账册、潮气和秦老板的反应压成一条开场说明。",
    )
    compressed_context = service.build_keeper_compressed_context_from_context_pack(context_pack)

    serialized_pack = str(context_pack.model_dump(mode="json"))
    serialized_compressed = str(compressed_context.model_dump(mode="json"))

    assert compressed_context.situation_summary
    assert compressed_context.immediate_pressures
    assert compressed_context.next_focus
    assert len(serialized_compressed) < len(serialized_pack)
    assert "private_notes" not in serialized_compressed
    assert "secret_state_refs" not in serialized_compressed
    assert "participants" not in serialized_compressed


def test_web_app_experimental_ai_demo_run_keeps_kp_and_investigator_inputs_isolated(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/run",
        data={"investigator_id": "investigator-1"},
    )

    assert response.status_code == 200
    html = response.text
    assert "AI KP + AI Investigator Demo Harness" in html
    assert "已生成第 1 轮 isolated experimental AI demo 输出" in html
    assert "AI KP Demo Output" in html
    assert "AI KP 剧情支架提案" in html
    assert "AI KP 输入来源" in html
    assert "本次 AI KP 实验输出仅基于 keeper-side Compressed Context 与最多 3 条近期事件摘要。" in html
    assert "AI Investigator Demo Output" in html
    assert "AI Investigator 行动提案" in html
    assert "AI Investigator 输入来源" in html
    assert "本次 AI investigator 实验输出只基于所选调查员的可见状态摘要。" in html
    assert "本轮 continuity 来源" not in html
    assert "不含 keeper-only 信息" in html
    assert "experimental / non-authoritative" in html
    assert "为下一轮补充上一轮实际结果 / Keeper 采纳情况" in html
    assert "当前页实验评估" in html
    assert "AI KP：scene framing 连贯性" in html
    assert 'action="/app/sessions/' in html
    assert 'name="narrative_work_note"' in html
    assert 'name="evaluation_label"' in html
    assert 'name="evaluation_note"' in html
    assert 'name="keeper_turn_outcome_note"' in html
    assert 'name="visible_turn_outcome_note"' in html
    assert 'name="current_turn_index" value="1"' in html
    assert len(fake_service.requests) == 2
    kp_request = fake_service.requests[0]
    investigator_request = fake_service.requests[1]
    assert kp_request.workspace_key == "experimental_ai_kp_demo"
    assert kp_request.task_key == "demo_loop"
    assert "compressed_context" in kp_request.context
    assert "recent_event_lines" in kp_request.context
    assert "private_notes" not in str(kp_request.context)
    assert "secret_state_refs" not in str(kp_request.context)
    assert "participants" not in str(kp_request.context)
    assert investigator_request.workspace_key == "experimental_ai_investigator_demo"
    assert investigator_request.task_key == "demo_loop"
    assert investigator_request.context["viewer"]["actor_id"] == "investigator-1"
    assert investigator_request.context["session"]["current_scene"] == "旅店账房"
    assert investigator_request.context["visible_clues"]
    assert investigator_request.context["recent_events"]
    assert "compressed_context" not in investigator_request.context
    assert "context_pack" not in investigator_request.context
    serialized_investigator_context = str(investigator_request.context)
    assert "private_notes" not in serialized_investigator_context
    assert "secret_state_refs" not in serialized_investigator_context
    assert "own_private_state" not in serialized_investigator_context
    assert "keeper_workflow" not in serialized_investigator_context
    assert "private_notes" not in html
    assert "secret_state_refs" not in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_experimental_ai_demo_next_turn_uses_page_local_continuity_bridge_without_secret_leak(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    first_response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/run",
        data={"investigator_id": "investigator-1"},
    )

    assert first_response.status_code == 200

    second_response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/run",
        data={
            "investigator_id": "investigator-1",
            "current_turn_index": "1",
            "previous_kp_title": "AI KP 剧情支架提案",
            "previous_kp_summary": "这是 experimental / non-authoritative 的 AI KP 候选叙事输出。",
            "previous_kp_draft_excerpt": "KP 可先用潮气、旧账册和老板的短暂失态开场。",
            "previous_investigator_title": "AI Investigator 行动提案",
            "previous_investigator_summary": "这是 experimental / non-authoritative 的调查员行动提案。",
            "previous_investigator_draft_excerpt": "调查员会先盯住账册缺页和住客编号。",
            "keeper_turn_outcome_note": "Keeper 实际采纳了老板先否认、再对 204 房登记表现回避，并把压力推进到二楼脚步声。",
            "visible_turn_outcome_note": "老板回避 204 房登记，调查员注意到账册缺页和二楼脚步声。",
        },
    )

    assert second_response.status_code == 200
    html = second_response.text
    assert "已生成第 2 轮 isolated experimental AI demo 输出" in html
    assert "页内 continuity bridge" in html
    assert html.count("本轮 continuity 来源") == 2
    assert "本轮已参考上一轮 continuity bridge。" in html
    assert "已纳入 keeper-side continuity note。" in html
    assert "已纳入公开可见 continuity note。" in html
    assert "本轮已参考上一轮公开 continuity bridge。" in html
    assert "输入只含当前页公开可见 continuity 摘要，不含 keeper-side continuity。" in html
    assert 'name="current_turn_index" value="2"' in html
    assert len(fake_service.requests) == 4
    kp_request = fake_service.requests[2]
    investigator_request = fake_service.requests[3]
    assert kp_request.workspace_key == "experimental_ai_kp_demo"
    assert investigator_request.workspace_key == "experimental_ai_investigator_demo"
    assert kp_request.context["turn_bridge"]["previous_turn_index"] == 1
    assert kp_request.context["turn_bridge"]["keeper_adoption_and_outcome_note"].startswith("Keeper 实际采纳了")
    assert kp_request.context["turn_bridge"]["public_outcome_note"].startswith("老板回避 204 房登记")
    assert kp_request.context["turn_bridge"]["previous_ai_kp"]["title"] == "AI KP 剧情支架提案"
    assert kp_request.context["turn_bridge"]["previous_ai_investigator"]["title"] == "AI Investigator 行动提案"
    assert investigator_request.context["turn_bridge"]["previous_turn_index"] == 1
    assert investigator_request.context["turn_bridge"]["public_outcome_note"].startswith("老板回避 204 房登记")
    serialized_investigator_context = str(investigator_request.context)
    assert "keeper_adoption_and_outcome_note" not in serialized_investigator_context
    assert "Keeper 实际采纳了" not in serialized_investigator_context
    assert "compressed_context" not in serialized_investigator_context
    assert "private_notes" not in serialized_investigator_context
    assert "secret_state_refs" not in serialized_investigator_context
    assert "Keeper 实际采纳了老板先否认" not in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_experimental_ai_demo_result_page_can_trigger_continuity_bridge_drafting(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service

    response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/run",
        data={"investigator_id": "investigator-1"},
    )

    assert response.status_code == 200
    html = response.text
    assert 'formaction="/app/sessions/' in html
    assert '/experimental-ai-demo/draft-continuity"' in html
    assert "起草 continuity bridge 草稿" in html
    assert 'name="current_kp_result_json"' in html
    assert 'name="current_investigator_result_json"' in html
    assert 'name="narrative_work_note"' in html
    assert 'name="keeper_turn_outcome_note"' in html
    assert 'name="visible_turn_outcome_note"' in html
    assert "keeper draft 起草来源" not in html
    assert "visible draft 起草来源" not in html
    assert "运行 self-play 预演链" in html
    assert len(fake_service.requests) == 2


def test_web_app_experimental_ai_demo_self_play_preview_runs_ordered_chain_and_prefills_dual_drafts(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/self-play-preview",
        data={
            "investigator_id": "investigator-1",
            "current_turn_index": "1",
            "previous_kp_title": "AI KP 剧情支架提案",
            "previous_kp_summary": "这是 experimental / non-authoritative 的 AI KP 候选叙事输出。",
            "previous_kp_draft_excerpt": "KP 可先用潮气、旧账册和老板的短暂失态开场。",
            "previous_investigator_title": "AI Investigator 行动提案",
            "previous_investigator_summary": "这是 experimental / non-authoritative 的调查员行动提案。",
            "previous_investigator_draft_excerpt": "调查员会先盯住账册缺页和住客编号。",
            "keeper_turn_outcome_note": "Keeper 实际采纳了老板先否认、再对 204 房登记表现回避，并把压力推进到二楼脚步声。",
            "visible_turn_outcome_note": "老板回避 204 房登记，调查员注意到账册缺页和二楼脚步声。",
            "evaluation_label": "self-play preview / continuity 写法 2",
            "evaluation_note": "观察一次点击串联后的连续性是否更顺。",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "已串行预演第 2 轮 self-play preview chain，并将 dual continuity drafts 回填到当前页 textarea；仍需 Keeper 人工审阅，不会自动提交或继续下一轮。" in html
    assert "Self-play Orchestration Preview" in html
    assert "Step 1 · AI KP preview" in html
    assert "Step 2 · AI investigator preview" in html
    assert "Step 3 · keeper continuity draft" in html
    assert "Step 4 · visible continuity draft" in html
    assert "来源：直接运行自 experimental AI KP demo block。" in html
    assert "来源：直接运行自 experimental AI investigator demo block。" in html
    assert "来源：直接运行自 experimental keeper continuity drafting block。" in html
    assert "来源：直接运行自 experimental visible continuity drafting block。" in html
    assert html.count("说明：这是当前页 orchestration preview step，不是已执行结果。") == 4
    assert f'data-adopt-target="experimental-narrative-work-note-{session_id}"' in html
    assert f'data-adopt-target="experimental-keeper-turn-outcome-note-{session_id}"' in html
    assert f'data-adopt-target="experimental-visible-turn-outcome-note-{session_id}"' in html
    assert "当前 handoff 目标：当前页 narrative_work_note。" in html
    assert "preview 完成后已回填该 textarea；如需用当前预演版本覆盖 working text，可重新带入。" in html
    assert "keeper draft 起草来源" in html
    assert "visible draft 起草来源" in html
    assert "已填入 keeper continuity bridge 草稿；仍需人工审阅、修改或清空。" in html
    assert "已填入公开 continuity bridge 草稿；仍需人工审阅、修改或清空。" in html
    assert "Keeper 暂定保留账册缺页、老板回避和二楼脚步声作为下一轮内部 continuity" in html
    assert "调查员目前只确认了账册缺页、老板回避和二楼脚步声" in html
    assert 'name="current_narrative_work_note" value=""' in html
    assert 'name="current_turn_index" value="2"' in html
    assert "生成下一轮实验回合" in html
    assert len(fake_service.requests) == 4
    kp_request = fake_service.requests[0]
    investigator_request = fake_service.requests[1]
    keeper_draft_request = fake_service.requests[2]
    visible_draft_request = fake_service.requests[3]
    assert kp_request.workspace_key == "experimental_ai_kp_demo"
    assert investigator_request.workspace_key == "experimental_ai_investigator_demo"
    assert keeper_draft_request.workspace_key == "experimental_ai_keeper_continuity_draft"
    assert visible_draft_request.workspace_key == "experimental_ai_visible_continuity_draft"
    assert kp_request.context["turn_bridge"]["previous_turn_index"] == 1
    assert kp_request.context["turn_bridge"]["keeper_adoption_and_outcome_note"].startswith("Keeper 实际采纳了")
    assert kp_request.context["turn_bridge"]["public_outcome_note"].startswith("老板回避 204 房登记")
    assert "compressed_context" not in investigator_request.context
    serialized_investigator_context = str(investigator_request.context)
    assert "private_notes" not in serialized_investigator_context
    assert "secret_state_refs" not in serialized_investigator_context
    assert "keeper_workflow" not in serialized_investigator_context
    assert "compressed_context" in keeper_draft_request.context
    assert "current_ai_kp_output" in keeper_draft_request.context
    assert "current_ai_investigator_output" in keeper_draft_request.context
    assert keeper_draft_request.context["evaluation_hint"]["label"] == "self-play preview / continuity 写法 2"
    assert "compressed_context" not in visible_draft_request.context
    assert "current_ai_kp_output" not in visible_draft_request.context
    serialized_visible_context = str(visible_draft_request.context)
    assert "private_notes" not in serialized_visible_context
    assert "secret_state_refs" not in serialized_visible_context
    assert "keeper_workflow" not in serialized_visible_context
    assert "Keeper 实际采纳了" not in serialized_visible_context
    assert "private_notes" not in html
    assert "secret_state_refs" not in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_experimental_ai_demo_preview_narrative_handoff_stays_manual_and_page_local(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)
    manual_note = "手工 narrative 备注：先压潮气和旧账册，再把压力推向 204 房。"

    response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/self-play-preview",
        data={
            "investigator_id": "investigator-1",
            "narrative_work_note": manual_note,
        },
    )

    assert response.status_code == 200
    html = response.text
    assert 'name="narrative_work_note"' in html
    narrative_section = html.split(
        f'id="experimental-narrative-work-note-{session_id}"',
        1,
    )[1].split("</textarea>", 1)[0]
    assert manual_note in narrative_section
    assert "KP 可先用潮气、旧账册和老板的短暂失态开场，再把压力推向缺页登记。" not in narrative_section
    assert "KP 可先用潮气、旧账册和老板的短暂失态开场，再把压力推向缺页登记。" in html
    assert (
        f'name="current_narrative_work_note" value="{manual_note}"'
        in html
    )
    assert f'data-adopt-target="experimental-narrative-work-note-{session_id}"' in html
    for llm_request in fake_service.requests:
        assert manual_note not in str(llm_request.context)
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_experimental_ai_demo_draft_continuity_prefills_dual_textareas_without_state_mutation(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)
    kp_result = _make_experimental_result(
        workspace_key="experimental_ai_kp_demo",
        task_label="AI KP 剧情支架提案",
        title="AI KP 剧情支架提案",
        summary="这是 experimental / non-authoritative 的 AI KP 候选叙事输出。",
    )
    investigator_result = _make_experimental_result(
        workspace_key="experimental_ai_investigator_demo",
        task_label="AI Investigator 行动提案",
        title="AI Investigator 行动提案",
        summary="这是 experimental / non-authoritative 的调查员行动提案。",
    )

    response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/draft-continuity",
        data={
            "investigator_id": "investigator-1",
            "current_turn_index": "1",
            "current_kp_result_json": kp_result.model_dump_json(),
            "current_investigator_result_json": investigator_result.model_dump_json(),
            "current_kp_has_keeper_continuity": "1",
            "current_kp_has_visible_continuity": "1",
            "current_investigator_has_visible_continuity": "1",
            "evaluation_label": "continuity 写法 2",
            "evaluation_note": "先验证 continuity 草稿是否足够顺手。",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "已起草 continuity bridge 草稿并填入当前页 textarea；仍需 Keeper 人工审阅、修改并手工触发下一轮。" in html
    assert "已填入 keeper continuity bridge 草稿；仍需人工审阅、修改或清空。" in html
    assert "已填入公开 continuity bridge 草稿；仍需人工审阅、修改或清空。" in html
    assert "keeper draft 起草来源" in html
    assert "本次 keeper continuity draft 已参考当前 Compressed Context。" in html
    assert "已纳入当前轮 AI KP 输出摘要与 AI investigator 输出摘要。" in html
    assert "已参考当前页实验标签 / 评估备注。" in html
    assert "visible draft 起草来源" in html
    assert "本次 visible continuity draft 已参考当前 investigator visible summary。" in html
    assert "已纳入 recent visible events 与当前轮 AI investigator 输出摘要。" in html
    assert "Keeper 暂定保留账册缺页、老板回避和二楼脚步声作为下一轮内部 continuity" in html
    assert "调查员目前只确认了账册缺页、老板回避和二楼脚步声" in html
    assert 'name="keeper_turn_outcome_note"' in html
    assert 'name="visible_turn_outcome_note"' in html
    assert 'name="evaluation_label" value="continuity 写法 2"' in html
    assert len(fake_service.requests) == 2
    keeper_request = fake_service.requests[0]
    visible_request = fake_service.requests[1]
    assert keeper_request.workspace_key == "experimental_ai_keeper_continuity_draft"
    assert visible_request.workspace_key == "experimental_ai_visible_continuity_draft"
    assert "compressed_context" in keeper_request.context
    assert "current_ai_kp_output" in keeper_request.context
    assert "current_ai_investigator_output" in keeper_request.context
    assert keeper_request.context["evaluation_hint"]["label"] == "continuity 写法 2"
    assert "compressed_context" not in visible_request.context
    assert "current_ai_kp_output" not in visible_request.context
    serialized_visible_context = str(visible_request.context)
    assert "private_notes" not in serialized_visible_context
    assert "secret_state_refs" not in serialized_visible_context
    assert "keeper_workflow" not in serialized_visible_context
    assert "evaluation_hint" not in visible_request.context
    assert "Keeper 暂定保留账册缺页" not in serialized_visible_context
    visible_echo_section = html.split("visible draft 起草来源", 1)[1].split("</article>", 1)[0]
    assert "Compressed Context" not in visible_echo_section
    assert "实验标签" not in visible_echo_section
    assert "AI KP 输出摘要" not in visible_echo_section
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_experimental_ai_demo_evaluation_rubric_stays_page_local_and_non_authoritative(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    before_snapshot = _get_snapshot(client, session_id)
    kp_result = _make_experimental_result(
        workspace_key="experimental_ai_kp_demo",
        task_label="AI KP 剧情支架提案",
        title="AI KP 剧情支架提案",
        summary="这是 experimental / non-authoritative 的 AI KP 候选叙事输出。",
    )
    investigator_result = _make_experimental_result(
        workspace_key="experimental_ai_investigator_demo",
        task_label="AI Investigator 行动提案",
        title="AI Investigator 行动提案",
        summary="这是 experimental / non-authoritative 的调查员行动提案。",
    )

    response = client.post(
        f"/app/sessions/{session_id}/experimental-ai-demo/evaluate",
        data={
            "investigator_id": "investigator-1",
            "current_turn_index": "2",
            "current_kp_result_json": kp_result.model_dump_json(),
            "current_investigator_result_json": investigator_result.model_dump_json(),
            "current_kp_has_keeper_continuity": "1",
            "current_kp_has_visible_continuity": "1",
            "current_investigator_has_visible_continuity": "1",
            "evaluation_label": "continuity 写法 2 / 更激进的 KP framing",
            "kp_scene_coherence": "good",
            "kp_pressure_reasonableness": "mixed",
            "investigator_visible_fit": "good",
            "investigator_action_value": "mixed",
            "continuity_stability": "good",
            "drift_or_leak_risk": "good",
            "evaluation_note": "第二轮连续性更稳，但调查员提案略有重复。",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "已记录当前页实验评估" in html
    assert "当前页评估回显" in html
    assert "当前实验标签：continuity 写法 2 / 更激进的 KP framing" in html
    assert "AI KP：scene framing 连贯性：好" in html
    assert "AI KP：pressure / next beat 合理性：一般" in html
    assert "第二轮连续性更稳，但调查员提案略有重复。" in html
    assert "不会写入 authoritative state" in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot

    refresh_response = client.get(f"/app/sessions/{session_id}/experimental-ai-demo")

    assert refresh_response.status_code == 200
    refresh_html = refresh_response.text
    assert "已记录当前页实验评估" not in refresh_html
    assert "当前页评估回显" not in refresh_html
    assert "continuity 写法 2 / 更激进的 KP framing" not in refresh_html
    assert "第二轮连续性更稳，但调查员提案略有重复。" not in refresh_html


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
    assert "未解决事项 / 当前压力" in html
    assert "最近 Keeper 备注" in html
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


def test_web_app_keeper_narrative_scaffolding_generates_non_authoritative_draft(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/keeper/narrative-assistant",
        data={"assistant_task": "scene_framing", "narrative_note": "已有剧情草稿"},
    )

    assert response.status_code == 200
    html = response.text
    assert "AI-KP Narrative Scaffolding" in html
    assert "下一幕开场建议 结果" in html
    assert "这是非权威辅助输出。" in html
    assert "当前压缩输入来源" in html
    assert "本次 剧情支架建议优先参考当前 Compressed Context。" in html
    assert "压缩范围：当前局势、当前压力 / 未解决事项、当前最该推进方向。" in html
    assert "这是 keeper-side 工作压缩摘要输入，不是已执行结果，也不是 authoritative truth。" in html
    assert "当前输入来源" in html
    assert "本次 剧情支架建议基于当前 Keeper Context Pack。" in html
    assert "摘要范围：局势摘要、未解决事项、当前压力 / 线索方向、当前 narrative_work_note。" in html
    assert "这是 keeper-side 工作摘要输入，不是已执行结果，也不是 authoritative truth。" in html
    assert 'href="#keeper-context-pack"' in html
    assert "查看当前 Keeper Context Pack" in html
    assert "草稿类型：场景开场草稿" in html
    assert "推荐带入：剧情工作备注" in html
    assert "当前对象：当前会话" in html
    assert f"对象标识：{session_id}" in html
    assert "来源语境：基于当前 keeper workspace：旅店账房 / 核对账房记录。" in html
    assert "局部上下文：当前场景/beat、未完成目标、活跃 prompts、近期事件、战斗摘要与最多 4 条运行时提示。" in html
    assert '>带入当前剧情工作备注框</button>' in html
    assert f'data-adopt-target="narrative-work-note-{session_id}"' in html
    assert 'data-adopt-status-text="已带入 场景开场草稿。来源：基于当前 keeper workspace：旅店账房 / 核对账房记录。 当前仍需 Keeper 人工编辑并提交。"' in html
    assert 'data-adopt-flow-status-text="该草稿来自当前 keeper narrative scaffolding。已带入：当前剧情工作备注框。当前仍待 Keeper 人工编辑并提交。"' in html
    assert "当前尚未带入。若采纳，将带入当前剧情工作备注框，之后仍需 Keeper 人工编辑并提交。" in html
    assert "已有剧情草稿" in html
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.workspace_key == "keeper_narrative_scaffolding"
    assert request.task_key == "scene_framing"
    assert "context_pack" in request.context
    assert "compressed_context" in request.context
    assert request.context["context_pack"]["identity"]["current_scene"] == "旅店账房"
    assert request.context["compressed_context"]["current_scene"] == "旅店账房"
    assert request.context["compressed_context"]["situation_summary"]
    assert request.context["compressed_context"]["next_focus"]
    assert request.context["context_pack"]["prompt_lines"]
    assert request.context["context_pack"]["open_threads"]
    assert request.context["session"]["current_scene"] == "旅店账房"
    assert request.context["active_prompts"]
    assert "runtime_hints" in request.context
    assert "knowledge_hints" in request.context["runtime_hints"]
    serialized_pack = str(request.context["context_pack"])
    serialized_compressed = str(request.context["compressed_context"])
    assert len(serialized_compressed) < len(serialized_pack)
    assert "private_notes" not in serialized_pack
    assert "secret_state_refs" not in serialized_pack
    assert "participants" not in serialized_pack
    assert "private_notes" not in serialized_compressed
    assert "secret_state_refs" not in serialized_compressed
    assert "participants" not in serialized_compressed
    assert "private_notes" not in html
    assert "secret_state_refs" not in html
    serialized_context = str(request.context)
    assert "private_notes" not in serialized_context
    assert "secret_state_refs" not in serialized_context
    assert "participants" not in serialized_context
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot


def test_web_app_keeper_narrative_note_submit_stays_non_authoritative(
    client: TestClient,
) -> None:
    session_id = _start_keeper_dashboard_session(client)
    _advance_keeper_dashboard_session(client, session_id)
    fake_service = _FakeLocalLLMService()
    client.app.state.local_llm_service = fake_service
    before_snapshot = _get_snapshot(client, session_id)

    response = client.post(
        f"/app/sessions/{session_id}/keeper/narrative-note",
        data={"narrative_note": "先把账房的潮气、旧账册和秦老板的视线压力一起摆出来。"},
    )

    assert response.status_code == 200
    html = response.text
    assert "当前剧情工作备注已人工确认" in html
    assert "不会写入 session 主状态" in html
    assert "当前剧情工作备注已人工提交，本轮剧情支架建议链已结束。" in html
    assert "先把账房的潮气、旧账册和秦老板的视线压力一起摆出来。" in html
    assert 'name="narrative_note"' in html
    assert "当前可采纳：场景开场草稿" not in html
    assert "带入当前剧情工作备注框" not in html
    assert "当前尚未带入。若采纳，将带入当前剧情工作备注框，之后仍需 Keeper 人工编辑并提交。" not in html
    assert "当前仍待 Keeper 人工编辑并提交。" not in html
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
    assert "当前页工作备注已人工提交，本轮 assistant 半手动链已结束。" in html
    assert "假说：204 房的住客登记和地窖搬运时间存在对应关系。" in html
    assert 'name="working_note"' in html
    assert "当前可采纳草稿" not in html
    assert "带入当前页工作备注框" not in html
    assert "当前尚未带入。若采纳，将带入当前页工作备注框，之后仍需人工编辑并提交。" not in html
    assert "当前仍待人工编辑并提交。" not in html
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
    assert "Keeper Context Pack" in html
    assert "Compact Recap / 压缩工作摘要" in html
    assert 'id="keeper-context-pack"' in html
    assert "当前局势一句话" in html
    assert "当前局势摘要" in html
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
    assert "当前压缩输入来源" in html
    assert "本次 recap 建议优先参考当前 Compressed Context。" in html
    assert "压缩范围：当前局势、当前压力 / 未解决事项、当前最该推进方向。" in html
    assert "这是 keeper-side 工作压缩摘要输入，不是已执行结果，也不是 authoritative truth。" in html
    assert "当前输入来源" in html
    assert "本次 recap 建议基于当前 Keeper Context Pack。" in html
    assert "摘要范围：局势摘要、未解决事项、当前压力 / 线索方向。" in html
    assert "这是 keeper-side 工作摘要输入，不是已执行结果，也不是 authoritative truth。" in html
    assert 'href="#keeper-context-pack"' in html
    assert "查看当前 Keeper Context Pack" in html
    after_snapshot = _get_snapshot(client, session_id)
    assert before_snapshot == after_snapshot
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.workspace_key == "session_recap"
    assert "context_pack" in request.context
    assert "compressed_context" in request.context
    assert request.context["context_pack"]["identity"]["current_scene"] == "旅店账房"
    assert request.context["compressed_context"]["current_scene"] == "旅店账房"
    assert request.context["compressed_context"]["situation_summary"]
    assert request.context["compressed_context"]["next_focus"]
    serialized_context = str(request.context)
    serialized_pack = str(request.context["context_pack"])
    serialized_compressed = str(request.context["compressed_context"])
    assert len(serialized_compressed) < len(serialized_pack)
    assert "private_notes" not in serialized_context
    assert "own_private_state" not in serialized_context
    assert "participants" not in str(request.context["context_pack"])
    assert "private_notes" not in serialized_compressed
    assert "secret_state_refs" not in serialized_compressed
    assert "participants" not in serialized_compressed
    assert "private_notes" not in html
    assert "secret_state_refs" not in html
