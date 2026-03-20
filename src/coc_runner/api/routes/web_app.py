from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from coc_runner.api.dependencies import (
    get_knowledge_service,
    get_local_llm_service,
    get_session_service,
)
from coc_runner.api.routes.playtest_setup import (
    _build_playtest_setup_request,
    _default_playtest_setup_form_values,
    _get_playtest_scenario_template,
    _normalize_playtest_setup_form_values,
    _playtest_scenario_templates,
)
from coc_runner.api.routes.playtest_shared import _normalize_form_text, _read_form_payload
from coc_runner.api.web_app_layout import render_web_app_shell
from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.application.local_llm_service import (
    LocalLLMAssistantRequest,
    LocalLLMAssistantResult,
    LocalLLMService,
)
from coc_runner.application.session_service import SessionService
from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    AdvanceCombatTurnRequest,
    AuditActionType,
    InvestigatorAttributeCheckRequest,
    InvestigatorDamageResolutionRequest,
    InvestigatorFirstAidRequest,
    InvestigatorMeleeAttackRequest,
    InvestigatorRangedAttackRequest,
    InvestigatorSanCheckRequest,
    InvestigatorSkillCheckRequest,
    KeeperWoundResolution,
    KeeperWoundResolutionRequest,
    ReviewDraftRequest,
    SessionStatus,
    StartCombatContextRequest,
    UpdateKeeperPromptRequest,
    UpdateSessionLifecycleRequest,
    ViewerRole,
)
from coc_runner.error_details import extract_error_detail


router = APIRouter(prefix="/app", tags=["web-app"])

KEEPER_ASSISTANT_TASKS: dict[str, str] = {
    "situation_summary": "当前局势摘要",
    "next_steps": "下一步建议",
    "note_draft": "主持人备注草稿",
    "draft_review_note_draft": "草稿审阅说明草稿",
}
KEEPER_ASSISTANT_TARGET_LABELS: dict[str, str] = {
    "prompt_note": "Prompt 备注",
    "draft_review_editor_notes": "草稿审阅说明",
}
KEEPER_ASSISTANT_TARGET_FIELD_LABELS: dict[str, str] = {
    "prompt_note": "当前 Prompt 备注框",
    "draft_review_editor_notes": "当前草稿审阅说明框",
}
KEEPER_ASSISTANT_TARGET_BY_TASK: dict[str, str] = {
    "note_draft": "prompt_note",
    "draft_review_note_draft": "draft_review_editor_notes",
}
KEEPER_ASSISTANT_DRAFT_KIND_LABELS: dict[str, str] = {
    "prompt_note_draft": "Prompt 备注草稿",
    "draft_review_note_draft": "草稿审阅说明草稿",
}
KEEPER_ASSISTANT_DRAFT_KIND_BY_TASK: dict[str, str] = {
    "note_draft": "prompt_note_draft",
    "draft_review_note_draft": "draft_review_note_draft",
}
KEEPER_ASSISTANT_SOURCE_CONTEXT_BY_KIND: dict[str, str] = {
    "prompt_note_draft": "基于当前 keeper workspace 摘要、待处理 prompts 与近期事件。",
    "draft_review_note_draft": "基于当前 keeper workspace 摘要与待审草稿概览。",
}
KEEPER_ASSISTANT_SOURCE_OBJECT_TYPE_LABELS: dict[str, str] = {
    "prompt": "单条 Prompt",
    "draft": "单条待审草稿",
}
KEEPER_ASSISTANT_DRAFT_SOURCE_ID = "keeper-assistant-draft-source"
KEEPER_NARRATIVE_TASKS: dict[str, str] = {
    "scene_framing": "下一幕开场建议",
    "clue_beat": "线索 / 下一拍建议",
    "npc_pressure": "NPC 反应 / 压力建议",
}
KEEPER_NARRATIVE_TARGET_LABELS: dict[str, str] = {
    "narrative_work_note": "剧情工作备注",
}
KEEPER_NARRATIVE_TARGET_FIELD_LABELS: dict[str, str] = {
    "narrative_work_note": "当前剧情工作备注框",
}
KEEPER_NARRATIVE_TARGET_BY_TASK: dict[str, str] = {
    "scene_framing": "narrative_work_note",
    "clue_beat": "narrative_work_note",
    "npc_pressure": "narrative_work_note",
}
KEEPER_NARRATIVE_DRAFT_KIND_LABELS: dict[str, str] = {
    "scene_framing_note_draft": "场景开场草稿",
    "clue_beat_note_draft": "线索 / 下一拍草稿",
    "npc_pressure_note_draft": "NPC 反应 / 压力草稿",
}
KEEPER_NARRATIVE_DRAFT_KIND_BY_TASK: dict[str, str] = {
    "scene_framing": "scene_framing_note_draft",
    "clue_beat": "clue_beat_note_draft",
    "npc_pressure": "npc_pressure_note_draft",
}
KEEPER_NARRATIVE_SOURCE_ID = "keeper-narrative-draft-source"
KNOWLEDGE_ASSISTANT_TASKS: dict[str, str] = {
    "source_summary": "资料摘要",
    "follow_up_questions": "可追问问题",
}
KNOWLEDGE_ASSISTANT_TARGET_LABELS: dict[str, str] = {
    "knowledge_work_note": "知识工作备注",
}
KNOWLEDGE_ASSISTANT_TARGET_FIELD_LABELS: dict[str, str] = {
    "knowledge_work_note": "当前页工作备注框",
}
KNOWLEDGE_ASSISTANT_TARGET_BY_TASK: dict[str, str] = {
    "source_summary": "knowledge_work_note",
    "follow_up_questions": "knowledge_work_note",
}
KNOWLEDGE_ASSISTANT_DRAFT_KIND_LABELS: dict[str, str] = {
    "knowledge_summary_note_draft": "资料摘要草稿",
    "knowledge_follow_up_note_draft": "追问问题草稿",
}
KNOWLEDGE_ASSISTANT_DRAFT_KIND_BY_TASK: dict[str, str] = {
    "source_summary": "knowledge_summary_note_draft",
    "follow_up_questions": "knowledge_follow_up_note_draft",
}
KNOWLEDGE_ASSISTANT_SOURCE_ID = "knowledge-assistant-draft-source"
RECAP_ASSISTANT_TASKS: dict[str, str] = {
    "recap_draft": "本局 recap 草稿",
    "open_loops": "待办与悬而未决事项",
}


def _status_label(status_value: Any) -> str:
    return {
        SessionStatus.PLANNED.value: "计划中",
        SessionStatus.ACTIVE.value: "进行中",
        SessionStatus.PAUSED.value: "已暂停",
        SessionStatus.COMPLETED.value: "已完成",
    }.get(str(status_value or ""), str(status_value or "未知"))


def _status_tone(status_value: Any) -> str:
    return {
        SessionStatus.ACTIVE.value: "success",
        SessionStatus.PAUSED.value: "warn",
        SessionStatus.COMPLETED.value: "danger",
    }.get(str(status_value or ""), "")


def _status_pill(status_value: Any) -> str:
    tone = _status_tone(status_value)
    tone_class = f" {tone}" if tone else ""
    return (
        f'<span class="status-pill{tone_class}">{escape(_status_label(status_value))} '
        f'<span class="mono">{escape(str(status_value or "unknown"))}</span></span>'
    )


def _group_label(group_value: Any, *, empty_label: str = "未分组") -> str:
    normalized = str(group_value or "").strip()
    return normalized or empty_label


def _excerpt(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _human_investigators(
    participants: list[dict[str, Any]],
    *,
    keeper_id: str | None = None,
) -> list[dict[str, Any]]:
    return [
        participant
        for participant in participants
        if isinstance(participant, dict)
        and participant.get("kind") == "human"
        and participant.get("actor_id") != keeper_id
    ]


def _participant_map(participants: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(participant.get("actor_id") or ""): participant
        for participant in participants
        if isinstance(participant, dict) and participant.get("actor_id")
    }


def _scene_and_beat(snapshot: dict[str, Any]) -> tuple[str, str | None, str | None]:
    current_scene = snapshot.get("current_scene") or {}
    progress_state = snapshot.get("progress_state") or {}
    scenario = snapshot.get("scenario") or {}
    beat_id = progress_state.get("current_beat")
    beats_by_id = {
        str(beat.get("beat_id")): beat
        for beat in scenario.get("beats") or []
        if isinstance(beat, dict) and beat.get("beat_id")
    }
    beat = beats_by_id.get(str(beat_id)) if beat_id is not None else None
    return (
        str(current_scene.get("title") or "未知场景"),
        str(beat_id) if beat_id is not None else None,
        str((beat or {}).get("title") or "") or None,
    )


def _detail_block(detail: dict[str, Any] | str | None) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return (
            '<section class="detail-error">'
            "<h2>加载失败</h2>"
            f"<p>{escape(detail)}</p>"
            "</section>"
        )
    errors = "".join(
        f"<li>{escape(str(error.get('message') or ''))}</li>"
        for error in detail.get("errors", [])
        if error.get("message")
    )
    code = detail.get("code")
    return (
        '<section class="detail-error">'
        "<h2>加载失败</h2>"
        f"<p>{escape(str(detail.get('message') or '请求未能完成'))}</p>"
        f'{f"<p class=\"meta-line\">code: {escape(str(code))}</p>" if code else ""}'
        f"{f'<ul>{errors}</ul>' if errors else ''}"
        "</section>"
    )


def _notice_block(notice: str | None) -> str:
    if not notice:
        return ""
    return f"""
      <section class="notice-panel success">
        <h2>操作已提交</h2>
        <p>{escape(notice)}</p>
      </section>
    """


def _bool_label(value: Any) -> str:
    return "是" if bool(value) else "否"


def _roll_outcome_label(outcome_value: Any) -> str:
    return {
        "critical_success": "大成功",
        "extreme_success": "极难成功",
        "hard_success": "困难成功",
        "regular_success": "成功",
        "failure": "失败",
        "fumble": "大失败",
    }.get(str(outcome_value or ""), str(outcome_value or "未知"))


def _attack_resolution_label(resolution_value: Any) -> str:
    return {
        "hit": "命中",
        "miss": "未命中",
        "dodge_success": "闪避成功",
        "counterattack_success": "反击成功",
        "kp_review": "平手待 KP 裁定",
    }.get(str(resolution_value or ""), str(resolution_value or "未知"))


def _defense_mode_label(defense_mode: Any) -> str:
    return {
        "dodge": "闪避",
        "counterattack": "反击",
    }.get(str(defense_mode or ""), str(defense_mode or "未知"))


def _rescue_window_label(payload: dict[str, Any]) -> str:
    return "开启" if payload.get("rescue_window_open") else "关闭"


def _render_feedback_panel(*, title: str, lines: list[str]) -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in lines if str(line).strip())
    if not items:
        return ""
    return f"""
      <section class="feedback-panel success">
        <h2>{escape(title)}</h2>
        <ul>{items}</ul>
      </section>
    """


def _parse_investigator_dice_modifier(dice_modifier: str) -> tuple[int, int]:
    if dice_modifier == "bonus_1":
        return (1, 0)
    if dice_modifier == "bonus_2":
        return (2, 0)
    if dice_modifier == "penalty_1":
        return (0, 1)
    if dice_modifier == "penalty_2":
        return (0, 2)
    if dice_modifier == "normal":
        return (0, 0)
    raise ValueError("invalid investigator dice modifier")


def _parse_investigator_ranged_attack_modifier(
    ranged_modifier: str,
) -> tuple[int, int, str]:
    if ranged_modifier == "aim_bonus_1":
        return (1, 0, "瞄准一轮")
    if ranged_modifier == "hurried_penalty_1":
        return (0, 1, "仓促射击")
    if ranged_modifier == "burst_penalty_1":
        return (0, 1, "连发压制")
    if ranged_modifier == "normal":
        return (0, 0, "普通攻击")
    raise ValueError("invalid ranged attack modifier")


def _investigator_skill_options(
    participant: dict[str, Any] | None,
    own_character_state: dict[str, Any],
) -> list[tuple[str, int]]:
    character = (
        participant.get("character")
        if isinstance(participant, dict) and isinstance(participant.get("character"), dict)
        else {}
    )
    merged_skills: dict[str, int] = {}
    character_skills = character.get("skills")
    if isinstance(character_skills, dict):
        for skill_name, score in character_skills.items():
            normalized = str(skill_name).strip()
            if normalized:
                merged_skills[normalized] = int(score)
    baseline_skills = own_character_state.get("skill_baseline")
    if isinstance(baseline_skills, dict):
        for skill_name, score in baseline_skills.items():
            normalized = str(skill_name).strip()
            if normalized:
                merged_skills.setdefault(normalized, int(score))
    return sorted(merged_skills.items(), key=lambda item: (-item[1], item[0]))


def _investigator_first_aid_skill_options(
    participant: dict[str, Any] | None,
    own_character_state: dict[str, Any],
) -> list[tuple[str, int]]:
    return [
        (skill_name, skill_value)
        for skill_name, skill_value in _investigator_skill_options(
            participant,
            own_character_state,
        )
        if skill_name in {"急救", "医学"}
    ]


def _investigator_attribute_options(
    participant: dict[str, Any] | None,
) -> list[tuple[str, str, int]]:
    character = (
        participant.get("character")
        if isinstance(participant, dict) and isinstance(participant.get("character"), dict)
        else {}
    )
    attributes = character.get("attributes") if isinstance(character.get("attributes"), dict) else {}
    pairs = [
        ("strength", "力量"),
        ("constitution", "体质"),
        ("size", "体型"),
        ("dexterity", "敏捷"),
        ("appearance", "外貌"),
        ("intelligence", "智力"),
        ("power", "意志"),
        ("education", "教育"),
    ]
    return [
        (attribute_name, label, int(attributes[attribute_name]))
        for attribute_name, label in pairs
        if attribute_name in attributes
    ]


def _investigator_target_options(participants: list[dict[str, Any]]) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for participant in participants:
        actor_id = str(participant.get("actor_id") or "").strip()
        display_name = str(participant.get("display_name") or actor_id).strip()
        if actor_id:
            options.append((actor_id, display_name or actor_id))
    return options


def _keeper_operator_id(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("keeper_id") or "keeper")


def _allowed_lifecycle_targets(current_status: Any) -> list[tuple[str, str]]:
    normalized = str(current_status or SessionStatus.PLANNED.value)
    transitions = {
        SessionStatus.PLANNED.value: [(SessionStatus.ACTIVE.value, "开始本局")],
        SessionStatus.ACTIVE.value: [
            (SessionStatus.PAUSED.value, "暂停"),
            (SessionStatus.COMPLETED.value, "结束本局"),
        ],
        SessionStatus.PAUSED.value: [
            (SessionStatus.ACTIVE.value, "恢复"),
            (SessionStatus.COMPLETED.value, "结束本局"),
        ],
        SessionStatus.COMPLETED.value: [],
    }
    return transitions.get(normalized, [])


def _assistant_task_selection(
    tasks: dict[str, str],
    selected_task: str | None,
) -> tuple[str, str]:
    if selected_task and selected_task in tasks:
        return selected_task, tasks[selected_task]
    first_task_key = next(iter(tasks))
    return first_task_key, tasks[first_task_key]


def _disabled_assistant_result(
    *,
    workspace_key: str,
    tasks: dict[str, str],
    selected_task: str | None,
) -> LocalLLMAssistantResult:
    task_key, task_label = _assistant_task_selection(tasks, selected_task)
    return LocalLLMAssistantResult(
        status="disabled",
        workspace_key=workspace_key,
        task_key=task_key,
        task_label=task_label,
        error_message="当前未启用本地 LLM；主流程不依赖它。",
    )


def _render_local_llm_assistant_output(
    result: LocalLLMAssistantResult | None,
) -> str:
    if result is None:
        return '<p class="empty">尚未生成辅助结果。</p>'
    if result.status == "disabled":
        return f"""
          <article class="list-card">
            <div class="list-head">
              <h3>Local LLM 未启用</h3>
              <span class="tag">disabled</span>
            </div>
            <p>{escape(str(result.error_message or "当前未启用本地 LLM。"))}</p>
          </article>
        """
    if result.status == "unavailable":
        return f"""
          <article class="list-card">
            <div class="list-head">
              <h3>Local LLM 当前不可用</h3>
              <span class="tag warn">fallback</span>
            </div>
            <p>{escape(str(result.error_message or "本地 LLM 当前不可用。"))}</p>
          </article>
        """
    assistant = result.assistant
    if assistant is None:
        return '<p class="empty">当前没有可显示的辅助内容。</p>'
    bullets = "".join(f"<li>{escape(item)}</li>" for item in assistant.bullets)
    suggested_questions = "".join(
        f"<li>{escape(item)}</li>" for item in assistant.suggested_questions
    )
    safety_notes = "".join(
        f"<li>{escape(item)}</li>" for item in assistant.safety_notes
    )
    provider_meta = " / ".join(
        item for item in [result.provider_name, result.model] if item
    )
    return f"""
      <article class="list-card">
        <div class="list-head">
          <h3>{escape(assistant.title)}</h3>
          <span class="tag success">non-authoritative</span>
        </div>
        <p>{escape(assistant.summary)}</p>
        {f'<p class="meta-line">provider: {escape(provider_meta)}</p>' if provider_meta else ''}
        {f'<ul class="meta-list">{bullets}</ul>' if bullets else ''}
        {f'<div class="divider"></div><p><strong>可继续追问</strong></p><ul class="meta-list">{suggested_questions}</ul>' if suggested_questions else ''}
        {f'<div class="divider"></div><p><strong>草稿</strong></p><p>{escape(assistant.draft_text or "")}</p>' if assistant.draft_text else ''}
        {f'<div class="divider"></div><p><strong>边界提醒</strong></p><ul class="meta-list">{safety_notes}</ul>' if safety_notes else ''}
      </article>
    """


def _keeper_assistant_adoption(
    result: LocalLLMAssistantResult | None,
    *,
    assistant_scope: dict[str, str] | None = None,
) -> dict[str, str] | None:
    if result is None or result.status != "success" or result.assistant is None:
        return None
    draft_text = _normalize_form_text(result.assistant.draft_text)
    if not draft_text:
        return None
    target = (
        _normalize_form_text((assistant_scope or {}).get("suggested_target"))
        or _normalize_form_text(result.assistant.suggested_target)
        or KEEPER_ASSISTANT_TARGET_BY_TASK.get(result.task_key)
    )
    if target not in KEEPER_ASSISTANT_TARGET_LABELS:
        return None
    draft_kind = (
        _normalize_form_text((assistant_scope or {}).get("draft_kind"))
        or _normalize_form_text(result.assistant.draft_kind)
        or KEEPER_ASSISTANT_DRAFT_KIND_BY_TASK.get(result.task_key)
    )
    if draft_kind not in KEEPER_ASSISTANT_DRAFT_KIND_LABELS:
        return None
    source_context_label = (
        _normalize_form_text((assistant_scope or {}).get("source_context_label"))
        or _normalize_form_text(result.assistant.source_context_label)
        or KEEPER_ASSISTANT_SOURCE_CONTEXT_BY_KIND.get(draft_kind)
        or "基于当前 keeper workspace 可见摘要。"
    )
    adoption = {
        "source_id": KEEPER_ASSISTANT_DRAFT_SOURCE_ID,
        "draft_text": draft_text,
        "target": target,
        "target_label": KEEPER_ASSISTANT_TARGET_LABELS[target],
        "target_field_label": KEEPER_ASSISTANT_TARGET_FIELD_LABELS.get(
            target,
            f"{KEEPER_ASSISTANT_TARGET_LABELS[target]}输入框",
        ),
        "draft_kind": draft_kind,
        "draft_kind_label": KEEPER_ASSISTANT_DRAFT_KIND_LABELS[draft_kind],
        "source_context_label": source_context_label,
    }
    if assistant_scope:
        for key in (
            "source_object_kind",
            "source_object_id",
            "source_object_label",
            "source_object_type_label",
        ):
            value = _normalize_form_text(assistant_scope.get(key))
            if value:
                adoption[key] = value
    return adoption


def _render_assistant_draft_source(
    *,
    assistant_scope: dict[str, str] | None,
    assistant_adoption: dict[str, str] | None,
) -> str:
    if assistant_adoption is None and assistant_scope is None:
        return ""
    object_lines = ""
    if assistant_scope:
        object_type_label = str(assistant_scope.get("source_object_type_label") or "当前对象")
        object_id = str(assistant_scope.get("source_object_id") or "—")
        object_label = str(assistant_scope.get("source_object_label") or object_id)
        scope_context = str(
            assistant_scope.get("source_context_label")
            or "基于当前 keeper workspace 可见摘要。"
        )
        local_context_summary = _normalize_form_text(assistant_scope.get("local_context_summary"))
        object_lines = f"""
          <li>当前对象：{escape(object_type_label)}</li>
          <li>对象标识：{escape(object_id)}</li>
          <li>对象标签：{escape(object_label)}</li>
          <li>来源语境：{escape(scope_context)}</li>
          {f'<li>局部上下文：{escape(local_context_summary)}</li>' if local_context_summary else ''}
        """
    draft_lines = ""
    hidden_source = ""
    if assistant_adoption is not None:
        hidden_source = f"""
          <textarea id="{escape(assistant_adoption['source_id'])}" class="assistant-draft-source" aria-hidden="true" tabindex="-1">{escape(assistant_adoption['draft_text'])}</textarea>
        """
        source_line = (
            f"<li>来源语境：{escape(assistant_adoption['source_context_label'])}</li>"
            if not assistant_scope
            else ""
        )
        draft_lines = f"""
          <li>草稿类型：{escape(assistant_adoption['draft_kind_label'])}</li>
          <li>推荐带入：{escape(assistant_adoption['target_label'])}</li>
          {source_line}
          <li>当前用途：供人工审阅后带入目标表单，再人工编辑并提交。</li>
        """
    return f"""
      {hidden_source}
      <article class="list-card">
        <div class="list-head">
          <h3>{'当前可采纳草稿' if assistant_adoption is not None else '当前生成上下文'}</h3>
          <span class="tag success">manual adoption</span>
        </div>
        <ul class="meta-list">
          {object_lines}
          {draft_lines}
        </ul>
      </article>
    """


def _render_assistant_adopt_button(
    *,
    assistant_adoption: dict[str, str] | None,
    target_kind: str,
    target_id: str,
    status_id: str,
    status_text: str | None = None,
    flow_status_id: str | None = None,
    flow_status_text: str | None = None,
    source_object_kind: str,
    source_object_id: str,
) -> str:
    if assistant_adoption is None or assistant_adoption.get("target") != target_kind:
        return ""
    scoped_object_kind = _normalize_form_text(assistant_adoption.get("source_object_kind"))
    scoped_object_id = _normalize_form_text(assistant_adoption.get("source_object_id"))
    if scoped_object_kind and scoped_object_id:
        if scoped_object_kind != source_object_kind or scoped_object_id != source_object_id:
            return ""
    target_field_label = assistant_adoption["target_field_label"]
    button_label = f"带入{target_field_label}"
    adopted_status_text = status_text or (
        f"已带入 {assistant_adoption['draft_kind_label']}。来源："
        f"{assistant_adoption['source_context_label']} 当前仍需 Keeper 人工编辑并提交。"
    )
    return f"""
      <div class="adoption-toolbar">
        <button
          class="button-button ghost"
          type="button"
          data-adopt-source="{escape(assistant_adoption['source_id'])}"
          data-adopt-target="{escape(target_id)}"
          data-adopt-status="{escape(status_id)}"
          data-adopt-status-text="{escape(adopted_status_text, quote=True)}"
          {f'data-adopt-flow-status="{escape(flow_status_id)}"' if flow_status_id else ''}
          {f'data-adopt-flow-status-text="{escape(flow_status_text or "", quote=True)}"' if flow_status_id else ''}
        >{escape(button_label)}</button>
      </div>
      <p class="helper">当前草稿用途：{escape(assistant_adoption['draft_kind_label'])}。将只带入{escape(target_field_label)}，不会自动提交。</p>
    """


def _assistant_targets_current_object(
    assistant_adoption: dict[str, str] | None,
    *,
    target_kind: str,
    source_object_kind: str,
    source_object_id: str,
) -> bool:
    if assistant_adoption is None or assistant_adoption.get("target") != target_kind:
        return False
    scoped_object_kind = _normalize_form_text(assistant_adoption.get("source_object_kind"))
    scoped_object_id = _normalize_form_text(assistant_adoption.get("source_object_id"))
    if scoped_object_kind and scoped_object_id:
        return scoped_object_kind == source_object_kind and scoped_object_id == source_object_id
    return True


def _build_keeper_completion_notices(
    action_result: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    prompt_notices: dict[str, str] = {}
    draft_notices: dict[str, str] = {}
    if not action_result:
        return prompt_notices, draft_notices
    kind = _normalize_form_text(action_result.get("kind"))
    target_id = _normalize_form_text(action_result.get("target_id"))
    if kind == "prompt_status" and target_id:
        prompt_notices[target_id] = "当前 Prompt 已人工提交，对象卡已恢复默认状态，不再显示上一轮待提交提示。"
    if kind == "draft_review" and target_id:
        draft_notices[target_id] = "当前草稿审阅已人工提交，对象卡已恢复默认状态，不再显示上一轮待提交提示。"
    return prompt_notices, draft_notices


def _keeper_narrative_scope_metadata(
    *,
    session_id: str,
    snapshot: dict[str, Any],
) -> dict[str, str]:
    scenario_title = _excerpt((snapshot.get("scenario") or {}).get("title"), limit=48) or session_id
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    beat_label = str(beat_title or beat_id or "未命名节点")
    return {
        "source_object_kind": "keeper_session",
        "source_object_id": session_id,
        "source_object_label": scenario_title,
        "source_object_type_label": "当前会话",
        "source_context_label": f"基于当前 keeper workspace：{current_scene} / {beat_label}。",
        "local_context_summary": "当前场景/beat、未完成目标、活跃 prompts、近期事件、战斗摘要与最多 4 条运行时提示。",
    }


def _keeper_narrative_assistant_fallback_text(
    result: LocalLLMAssistantResult,
) -> str | None:
    assistant = result.assistant
    if assistant is None:
        return None
    lines: list[str] = []
    summary = _normalize_form_text(assistant.summary)
    if summary:
        lines.append(summary)
    bullets = [item for item in assistant.bullets if item][:4]
    if bullets:
        lines.extend(f"- {item}" for item in bullets)
    questions = [item for item in assistant.suggested_questions if item][:2]
    if questions:
        lines.append("可继续追问：")
        lines.extend(f"- {item}" for item in questions)
    return "\n".join(lines).strip() or None


def _keeper_narrative_assistant_adoption(
    result: LocalLLMAssistantResult | None,
    *,
    assistant_scope: dict[str, str] | None = None,
) -> dict[str, str] | None:
    if result is None or result.status != "success" or result.assistant is None:
        return None
    draft_text = _normalize_form_text(result.assistant.draft_text) or _keeper_narrative_assistant_fallback_text(
        result
    )
    if not draft_text:
        return None
    target = (
        _normalize_form_text((assistant_scope or {}).get("suggested_target"))
        or _normalize_form_text(result.assistant.suggested_target)
        or KEEPER_NARRATIVE_TARGET_BY_TASK.get(result.task_key)
        or "narrative_work_note"
    )
    if target not in KEEPER_NARRATIVE_TARGET_LABELS:
        return None
    draft_kind = (
        _normalize_form_text((assistant_scope or {}).get("draft_kind"))
        or _normalize_form_text(result.assistant.draft_kind)
        or KEEPER_NARRATIVE_DRAFT_KIND_BY_TASK.get(result.task_key)
    )
    if draft_kind not in KEEPER_NARRATIVE_DRAFT_KIND_LABELS:
        return None
    source_context_label = (
        _normalize_form_text((assistant_scope or {}).get("source_context_label"))
        or _normalize_form_text(result.assistant.source_context_label)
        or "基于当前 keeper workspace 的场景与待处理上下文。"
    )
    adoption = {
        "source_id": KEEPER_NARRATIVE_SOURCE_ID,
        "draft_text": draft_text,
        "target": target,
        "target_label": KEEPER_NARRATIVE_TARGET_LABELS[target],
        "target_field_label": KEEPER_NARRATIVE_TARGET_FIELD_LABELS[target],
        "draft_kind": draft_kind,
        "draft_kind_label": KEEPER_NARRATIVE_DRAFT_KIND_LABELS[draft_kind],
        "source_context_label": source_context_label,
    }
    if assistant_scope:
        for key in (
            "source_object_kind",
            "source_object_id",
            "source_object_label",
            "source_object_type_label",
            "local_context_summary",
        ):
            value = _normalize_form_text(assistant_scope.get(key))
            if value:
                adoption[key] = value
    return adoption


def _knowledge_source_scope_metadata(source: dict[str, Any]) -> dict[str, str]:
    source_id = str(source.get("source_id") or "source")
    source_label = _excerpt(source.get("source_title_zh"), limit=48) or source_id
    return {
        "source_object_kind": "knowledge_source",
        "source_object_id": source_id,
        "source_object_label": source_label,
        "source_object_type_label": "当前资料",
        "source_context_label": f"基于当前资料：{source_label}（{source_id}）的摘要与预览。",
        "local_context_summary": "当前资料摘要、预览片段与已展示提取结果，不含未展示的 session 私密信息。",
    }


def _knowledge_assistant_fallback_text(result: LocalLLMAssistantResult) -> str | None:
    assistant = result.assistant
    if assistant is None:
        return None
    if result.task_key == "follow_up_questions":
        questions = [item for item in assistant.suggested_questions if item][:4]
        if questions:
            return "\n".join(f"- {question}" for question in questions)
    lines: list[str] = []
    summary = _normalize_form_text(assistant.summary)
    if summary:
        lines.append(summary)
    bullets = [item for item in assistant.bullets if item][:4]
    if bullets:
        lines.extend(f"- {bullet}" for bullet in bullets)
    questions = [item for item in assistant.suggested_questions if item][:3]
    if questions and result.task_key != "follow_up_questions":
        lines.append("可继续追问：")
        lines.extend(f"- {question}" for question in questions)
    return "\n".join(lines).strip() or None


def _knowledge_assistant_adoption(
    result: LocalLLMAssistantResult | None,
    *,
    source: dict[str, Any],
    assistant_scope: dict[str, str] | None = None,
) -> dict[str, str] | None:
    if result is None or result.status != "success" or result.assistant is None:
        return None
    draft_text = _normalize_form_text(result.assistant.draft_text) or _knowledge_assistant_fallback_text(
        result
    )
    if not draft_text:
        return None
    target = (
        _normalize_form_text(result.assistant.suggested_target)
        or KNOWLEDGE_ASSISTANT_TARGET_BY_TASK.get(result.task_key)
        or "knowledge_work_note"
    )
    if target not in KNOWLEDGE_ASSISTANT_TARGET_LABELS:
        return None
    draft_kind = (
        _normalize_form_text(result.assistant.draft_kind)
        or KNOWLEDGE_ASSISTANT_DRAFT_KIND_BY_TASK.get(result.task_key)
        or "knowledge_summary_note_draft"
    )
    if draft_kind not in KNOWLEDGE_ASSISTANT_DRAFT_KIND_LABELS:
        return None
    scope = assistant_scope or _knowledge_source_scope_metadata(source)
    source_context_label = (
        _normalize_form_text(result.assistant.source_context_label)
        or _normalize_form_text(scope.get("source_context_label"))
        or "基于当前资料摘要与预览。"
    )
    adoption = {
        "source_id": KNOWLEDGE_ASSISTANT_SOURCE_ID,
        "draft_text": draft_text,
        "target": target,
        "target_label": KNOWLEDGE_ASSISTANT_TARGET_LABELS[target],
        "target_field_label": KNOWLEDGE_ASSISTANT_TARGET_FIELD_LABELS[target],
        "draft_kind": draft_kind,
        "draft_kind_label": KNOWLEDGE_ASSISTANT_DRAFT_KIND_LABELS[draft_kind],
        "source_context_label": source_context_label,
    }
    for key in (
        "source_object_kind",
        "source_object_id",
        "source_object_label",
        "source_object_type_label",
    ):
        value = _normalize_form_text(scope.get(key))
        if value:
            adoption[key] = value
    return adoption


def _render_local_llm_assistant_panel(
    *,
    title: str,
    description: str,
    action: str,
    tasks: dict[str, str],
    selected_task: str | None,
    result: LocalLLMAssistantResult | None,
    hidden_fields: dict[str, str] | None = None,
    extra_output_html: str = "",
) -> str:
    task_key, _ = _assistant_task_selection(tasks, selected_task)
    options_html = "".join(
        f'<option value="{escape(task)}"{" selected" if task == task_key else ""}>{escape(label)}</option>'
        for task, label in tasks.items()
    )
    hidden_inputs = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(value, quote=True)}" />'
        for name, value in (hidden_fields or {}).items()
        if value is not None
    )
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>{escape(title)}</h2>
            <p>{escape(description)}</p>
          </div>
          <span class="tag">Local LLM</span>
        </div>
        <p class="helper">输出只作为摘要、建议或草稿，不会直接修改 authoritative state。即使本地 LLM 不可用，主流程也照常可用。</p>
        <div class="card-list">
          {_render_local_llm_assistant_output(result)}
          {extra_output_html}
        </div>
        <form method="post" action="{escape(action, quote=True)}" class="form-stack">
          {hidden_inputs}
          <label>
            辅助任务
            <select name="assistant_task">{options_html}</select>
          </label>
          <button class="button-button secondary" type="submit">生成非权威辅助内容</button>
        </form>
      </section>
    """


def _event_excerpt_items(events: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for event in events[:limit]:
        items.append(
            {
                "event_type": str(event.get("event_type") or "unknown"),
                "text": str(event.get("text") or ""),
            }
        )
    return items


def _build_keeper_assistant_context(
    *,
    snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
    runtime_assistance: dict[str, Any],
    san_aftermath_suggestions: dict[str, list[dict[str, Any]]],
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    participant_by_id = _participant_map(participants)
    character_states = {
        str(actor_id): state
        for actor_id, state in (snapshot.get("character_states") or {}).items()
        if isinstance(state, dict)
    }
    workflow = keeper_view.get("keeper_workflow") or {}
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    wound_follow_up: list[dict[str, Any]] = []
    for actor_id, state in character_states.items():
        flags = [
            label
            for flag, label in [
                ("heavy_wound_active", "重伤"),
                ("is_unconscious", "昏迷"),
                ("is_dying", "濒死"),
                ("is_stable", "已稳定"),
                ("rescue_window_open", "短时可救"),
                ("death_confirmed", "已死亡"),
            ]
            if state.get(flag)
        ]
        if flags:
            wound_follow_up.append(
                {
                    "actor_id": actor_id,
                    "display_name": str(
                        (participant_by_id.get(actor_id) or {}).get("display_name") or actor_id
                    ),
                    "current_hit_points": state.get("current_hit_points"),
                    "flags": flags,
                }
            )
    context = {
        "session": {
            "session_id": snapshot.get("session_id"),
            "scenario_title": (snapshot.get("scenario") or {}).get("title"),
            "status": snapshot.get("status"),
            "playtest_group": snapshot.get("playtest_group"),
            "current_scene": current_scene,
            "current_beat": beat_id,
            "current_beat_title": beat_title,
        },
        "workflow_summary_lines": list((workflow.get("summary") or {}).get("summary_lines") or [])[:6],
        "active_prompts": [
            {
                "prompt_text": prompt.get("prompt_text"),
                "category": prompt.get("category"),
                "priority": prompt.get("priority"),
                "status": prompt.get("status"),
                "notes": list(prompt.get("notes") or [])[:3],
            }
            for prompt in list(workflow.get("active_prompts") or [])[:5]
        ],
        "unresolved_objectives": [
            {
                "text": objective.get("text"),
                "scene_id": objective.get("scene_id"),
                "beat_id": objective.get("beat_id"),
            }
            for objective in list(workflow.get("unresolved_objectives") or [])[:5]
        ],
        "combat": {
            "current_actor_id": (snapshot.get("combat_context") or {}).get("current_actor_id"),
            "round_number": (snapshot.get("combat_context") or {}).get("round_number"),
            "turn_order_count": len((snapshot.get("combat_context") or {}).get("turn_order") or []),
        },
        "wound_follow_up": wound_follow_up[:5],
        "runtime_hints": {
            "rule_hints": [
                {
                    "title": item.get("title") or item.get("title_zh") or item.get("topic_key"),
                    "summary": _excerpt(item.get("summary") or item.get("content") or item.get("text"), limit=120),
                }
                for item in list(runtime_assistance.get("rule_hints") or [])[:3]
            ],
            "knowledge_hints": [
                {
                    "title": item.get("title") or item.get("title_zh") or item.get("topic_key"),
                    "summary": _excerpt(item.get("summary") or item.get("content") or item.get("text"), limit=120),
                }
                for item in list(runtime_assistance.get("knowledge_hints") or [])[:3]
            ],
        },
        "san_aftermath": [
            {
                "prompt_id": prompt_id,
                "suggestions": [str(item.get("label") or "") for item in items[:3]],
            }
            for prompt_id, items in list(san_aftermath_suggestions.items())[:4]
        ],
        "recent_events": _event_excerpt_items(list(reversed(keeper_view.get("visible_events") or [])), limit=5),
    }
    if context_pack:
        context["context_pack"] = context_pack
    return context


def _build_keeper_narrative_context(
    *,
    snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
    runtime_assistance: dict[str, Any],
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow = keeper_view.get("keeper_workflow") or {}
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    context = {
        "session": {
            "session_id": snapshot.get("session_id"),
            "scenario_title": (snapshot.get("scenario") or {}).get("title"),
            "status": snapshot.get("status"),
            "current_scene": current_scene,
            "current_beat": beat_id,
            "current_beat_title": beat_title,
        },
        "workflow_summary": [
            str(item)
            for item in list((workflow.get("summary") or {}).get("summary_lines") or [])[:4]
        ],
        "unresolved_objectives": [
            {
                "objective_id": item.get("objective_id"),
                "title": item.get("title"),
                "status": item.get("status"),
            }
            for item in list(workflow.get("unresolved_objectives") or [])[:3]
        ],
        "active_prompts": [
            {
                "prompt_id": item.get("prompt_id"),
                "category": item.get("category"),
                "status": item.get("status"),
                "prompt_text": _excerpt(item.get("prompt_text"), limit=120),
                "trigger_reason": _excerpt(item.get("trigger_reason"), limit=100),
            }
            for item in list(workflow.get("active_prompts") or [])[:3]
        ],
        "recent_events": _event_excerpt_items(
            list(reversed(keeper_view.get("visible_events") or [])),
            limit=4,
        ),
        "combat": {
            "current_actor_id": (snapshot.get("combat_context") or {}).get("current_actor_id"),
            "round_number": (snapshot.get("combat_context") or {}).get("round_number"),
            "turn_order_count": len((snapshot.get("combat_context") or {}).get("turn_order") or []),
        },
        "runtime_hints": {
            "rule_hints": [
                {
                    "title": item.get("title") or item.get("title_zh") or item.get("topic_key"),
                    "summary": _excerpt(
                        item.get("summary") or item.get("content") or item.get("text"),
                        limit=120,
                    ),
                }
                for item in list(runtime_assistance.get("rule_hints") or [])[:2]
            ],
            "knowledge_hints": [
                {
                    "title": item.get("title") or item.get("title_zh") or item.get("topic_key"),
                    "summary": _excerpt(
                        item.get("summary") or item.get("content") or item.get("text"),
                        limit=120,
                    ),
                }
                for item in list(runtime_assistance.get("knowledge_hints") or [])[:2]
            ],
        },
    }
    if context_pack:
        context["context_pack"] = context_pack
    return context


def _latest_audit_entry(
    snapshot: dict[str, Any],
    *,
    action: str | None = None,
    subject_id: str | None = None,
    detail_draft_id: str | None = None,
) -> dict[str, Any] | None:
    for entry in reversed(snapshot.get("audit_log") or []):
        if not isinstance(entry, dict):
            continue
        if action is not None and str(entry.get("action") or "") != action:
            continue
        if subject_id is not None and str(entry.get("subject_id") or "") != subject_id:
            continue
        details = entry.get("details") or {}
        if detail_draft_id is not None and str(details.get("draft_id") or "") != detail_draft_id:
            continue
        return entry
    return None


def _build_prompt_local_context(
    snapshot: dict[str, Any],
    prompt: dict[str, Any],
) -> dict[str, Any]:
    prompt_id = str(prompt.get("prompt_id") or "")
    current_status = _normalize_form_text(prompt.get("status"))
    current_category = _normalize_form_text(prompt.get("category"))
    latest_note = _normalize_form_text((prompt.get("notes") or [None])[-1]) if prompt.get("notes") else None
    recent_update_entry = _latest_audit_entry(
        snapshot,
        action=AuditActionType.KEEPER_PROMPT_UPDATED.value,
        subject_id=prompt_id,
    )
    recent_update_details = (recent_update_entry or {}).get("details") or {}
    recent_update = (
        {
            "status": recent_update_details.get("status"),
            "priority": recent_update_details.get("priority"),
            "note_count_added": recent_update_details.get("note_count_added"),
        }
        if recent_update_entry is not None
        else None
    )
    context_parts: list[str] = []
    if current_status:
        context_parts.append(f"当前状态：{current_status}")
    if current_category:
        context_parts.append(f"类别：{current_category}")
    if latest_note:
        context_parts.append(f"最近 note：{_excerpt(latest_note, limit=40)}")
    if recent_update is not None and recent_update.get("status"):
        context_parts.append(f"最近处理状态：{recent_update.get('status')}")
    return {
        "current_status": current_status,
        "current_category": current_category,
        "latest_note": latest_note,
        "recent_update": recent_update,
        "context_summary": " / ".join(context_parts) or None,
    }


def _build_draft_local_context(
    snapshot: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    draft_id = str(draft.get("draft_id") or "")
    current_review_status = draft.get("review_status")
    recent_review_entry = _latest_audit_entry(
        snapshot,
        action=AuditActionType.REVIEW_DECISION.value,
        detail_draft_id=draft_id,
    )
    recent_review_details = (recent_review_entry or {}).get("details") or {}
    recent_review = (
        {
            "decision": recent_review_details.get("decision"),
            "review_status": recent_review_details.get("review_status"),
            "editor_notes": recent_review_details.get("editor_notes"),
        }
        if recent_review_entry is not None
        else None
    )
    context_parts: list[str] = []
    if current_review_status:
        context_parts.append(f"当前 review 状态：{current_review_status}")
    if recent_review is not None and recent_review.get("decision"):
        context_parts.append(f"最近审阅动作：{recent_review.get('decision')}")
    recent_editor_note = _normalize_form_text((recent_review or {}).get("editor_notes"))
    if recent_editor_note:
        context_parts.append(f"最近 editor note：{_excerpt(recent_editor_note, limit=40)}")
    return {
        "current_review_status": current_review_status,
        "recent_review": recent_review,
        "context_summary": " / ".join(context_parts) or None,
    }


def _keeper_prompt_scope_metadata(
    prompt: dict[str, Any],
    *,
    local_context_summary: str | None = None,
) -> dict[str, str]:
    prompt_id = str(prompt.get("prompt_id") or "prompt")
    prompt_label = _excerpt(prompt.get("prompt_text"), limit=48) or prompt_id
    source_context_label = f"基于当前 prompt：{prompt_label}（{prompt_id}）。"
    if local_context_summary:
        source_context_label = f"基于当前 prompt：{prompt_label}（{prompt_id}）及最近处理上下文。"
    return {
        "source_object_kind": "prompt",
        "source_object_id": prompt_id,
        "source_object_label": prompt_label,
        "source_object_type_label": KEEPER_ASSISTANT_SOURCE_OBJECT_TYPE_LABELS["prompt"],
        "draft_kind": "prompt_note_draft",
        "suggested_target": "prompt_note",
        "source_context_label": source_context_label,
        "local_context_summary": local_context_summary or "",
    }


def _keeper_draft_scope_metadata(
    draft: dict[str, Any],
    *,
    local_context_summary: str | None = None,
) -> dict[str, str]:
    draft_id = str(draft.get("draft_id") or "draft")
    draft_label = _excerpt(draft.get("draft_text"), limit=48) or draft_id
    source_context_label = f"基于当前待审草稿：{draft_label}（{draft_id}）。"
    if local_context_summary:
        source_context_label = f"基于当前待审草稿：{draft_label}（{draft_id}）及最近审阅上下文。"
    return {
        "source_object_kind": "draft",
        "source_object_id": draft_id,
        "source_object_label": draft_label,
        "source_object_type_label": KEEPER_ASSISTANT_SOURCE_OBJECT_TYPE_LABELS["draft"],
        "draft_kind": "draft_review_note_draft",
        "suggested_target": "draft_review_editor_notes",
        "source_context_label": source_context_label,
        "local_context_summary": local_context_summary or "",
    }


def _render_prompt_generation_preview(
    prompt: dict[str, Any],
    *,
    local_context: dict[str, Any],
) -> str:
    prompt_context_display = _prompt_context_display(local_context)
    prompt_label = _excerpt(prompt.get("prompt_text"), limit=48) or str(
        prompt.get("prompt_id") or "当前 prompt"
    )
    return f"""
      <div class="assistant-context-preview">
        <p class="meta-line"><strong>本次生成将使用的局部上下文摘要</strong></p>
        <ul class="meta-list">
          <li>当前 Prompt：{escape(prompt_label)}</li>
          <li>当前状态 / 类别：{escape(prompt_context_display['status_category'])}</li>
          <li>最近 note：{escape(prompt_context_display['latest_note_label'])}</li>
          <li>最近处理摘要：{escape(prompt_context_display['recent_update_label'])}</li>
          <li>输入说明：本次草稿将基于当前 prompt 与最近处理上下文生成，不会直接执行任何动作。</li>
        </ul>
      </div>
    """


def _render_draft_generation_preview(
    draft: dict[str, Any],
    *,
    local_context: dict[str, Any],
) -> str:
    draft_context_display = _draft_context_display(local_context, draft=draft)
    draft_label = _excerpt(draft.get("draft_text"), limit=48) or str(
        draft.get("draft_id") or "当前草稿"
    )
    return f"""
      <div class="assistant-context-preview">
        <p class="meta-line"><strong>本次生成将使用的局部上下文摘要</strong></p>
        <ul class="meta-list">
          <li>当前草稿：{escape(draft_label)}</li>
          <li>当前 review 状态：{escape(draft_context_display['current_review_status'])}</li>
          <li>最近 editor note：{escape(draft_context_display['recent_editor_note_label'])}</li>
          <li>最近 review 摘要：{escape(draft_context_display['recent_review_label'])}</li>
          <li>输入说明：本次草稿将基于当前 draft 与最近审阅上下文生成，不会直接执行任何动作。</li>
        </ul>
      </div>
    """


def _prompt_context_display(local_context: dict[str, Any]) -> dict[str, str]:
    current_status = _normalize_form_text(local_context.get("current_status")) or "未标记"
    current_category = _normalize_form_text(local_context.get("current_category")) or "未标记"
    latest_note = _normalize_form_text(local_context.get("latest_note"))
    recent_update = local_context.get("recent_update") or {}
    recent_update_bits: list[str] = []
    if recent_update.get("status"):
        recent_update_bits.append(f"状态 {recent_update.get('status')}")
    if recent_update.get("priority") is not None:
        recent_update_bits.append(f"优先级 {recent_update.get('priority')}")
    note_count_added = recent_update.get("note_count_added")
    if note_count_added not in (None, "", 0):
        recent_update_bits.append(f"新增备注 {note_count_added}")
    recent_update_summary = " / ".join(recent_update_bits)
    latest_note_label = (
        f"已纳入（{_excerpt(latest_note, limit=28)}）"
        if latest_note
        else "无，将仅参考当前 Prompt"
    )
    recent_update_label = (
        f"已纳入（{recent_update_summary}）"
        if recent_update_summary
        else "无，将不额外引用处理摘要"
    )
    return {
        "status_category": f"{current_status} / {current_category}",
        "latest_note_label": latest_note_label,
        "recent_update_label": recent_update_label,
    }


def _draft_context_display(
    local_context: dict[str, Any],
    *,
    draft: dict[str, Any],
) -> dict[str, str]:
    current_review_status = _normalize_form_text(local_context.get("current_review_status")) or (
        _normalize_form_text(draft.get("review_status")) or "未标记"
    )
    recent_review = local_context.get("recent_review") or {}
    recent_editor_note = _normalize_form_text(recent_review.get("editor_notes"))
    recent_review_bits: list[str] = []
    if recent_review.get("decision"):
        recent_review_bits.append(f"决策 {recent_review.get('decision')}")
    if recent_review.get("review_status"):
        recent_review_bits.append(f"状态 {recent_review.get('review_status')}")
    recent_review_summary = " / ".join(recent_review_bits)
    recent_editor_note_label = (
        f"已纳入（{_excerpt(recent_editor_note, limit=28)}）"
        if recent_editor_note
        else "无，将仅参考当前待审草稿"
    )
    recent_review_label = (
        f"已纳入（{recent_review_summary}）"
        if recent_review_summary
        else "无，将不额外引用审阅摘要"
    )
    return {
        "current_review_status": current_review_status,
        "recent_editor_note_label": recent_editor_note_label,
        "recent_review_label": recent_review_label,
    }


def _assistant_scope_matches_object(
    assistant_scope: dict[str, str] | None,
    *,
    source_object_kind: str,
    source_object_id: str,
) -> bool:
    if not assistant_scope:
        return False
    return (
        _normalize_form_text(assistant_scope.get("source_object_kind")) == source_object_kind
        and _normalize_form_text(assistant_scope.get("source_object_id")) == source_object_id
    )


def _render_prompt_generation_source_echo(
    prompt: dict[str, Any],
    *,
    local_context: dict[str, Any],
    assistant_scope: dict[str, str] | None,
    assistant_adoption: dict[str, str] | None,
    prompt_id: str,
) -> str:
    if not _assistant_scope_matches_object(
        assistant_scope,
        source_object_kind="prompt",
        source_object_id=prompt_id,
    ):
        return ""
    prompt_label = _excerpt(prompt.get("prompt_text"), limit=48) or prompt_id
    context_display = _prompt_context_display(local_context)
    source_context_label = _normalize_form_text((assistant_scope or {}).get("source_context_label")) or (
        f"基于当前 prompt：{prompt_label}（{prompt_id}）。"
    )
    target_key = _normalize_form_text((assistant_adoption or {}).get("target")) or _normalize_form_text(
        (assistant_scope or {}).get("suggested_target")
    ) or "prompt_note"
    target_label = KEEPER_ASSISTANT_TARGET_LABELS.get(target_key, "Prompt 备注")
    flow_status_id = f"prompt-flow-status-{prompt_id}"
    return f"""
      <div class="assistant-source-echo">
        <p class="meta-line"><strong>本次已生成的来源回显</strong></p>
        <ul class="meta-list">
          <li>草稿归属：当前 Prompt {escape(prompt_label)}</li>
          <li>实际来源：{escape(source_context_label)}</li>
          <li>实际参考的局部字段：当前状态 / 类别（{escape(context_display['status_category'])}）</li>
          <li>最近 note：{escape(context_display['latest_note_label'])}</li>
          <li>最近处理摘要：{escape(context_display['recent_update_label'])}</li>
          <li>推荐带入目标：{escape(target_label)}</li>
          <li>边界说明：仅为草稿，不会自动提交，也不会改写 authoritative state。</li>
        </ul>
        <p id="{escape(flow_status_id)}" class="helper assistant-flow-status">当前尚未带入。若采纳，将带入{escape(target_label)}，之后仍需 Keeper 人工编辑并提交。</p>
      </div>
    """


def _render_draft_generation_source_echo(
    draft: dict[str, Any],
    *,
    local_context: dict[str, Any],
    assistant_scope: dict[str, str] | None,
    assistant_adoption: dict[str, str] | None,
    draft_id: str,
) -> str:
    if not _assistant_scope_matches_object(
        assistant_scope,
        source_object_kind="draft",
        source_object_id=draft_id,
    ):
        return ""
    draft_label = _excerpt(draft.get("draft_text"), limit=48) or draft_id
    context_display = _draft_context_display(local_context, draft=draft)
    source_context_label = _normalize_form_text((assistant_scope or {}).get("source_context_label")) or (
        f"基于当前待审草稿：{draft_label}（{draft_id}）。"
    )
    target_key = _normalize_form_text((assistant_adoption or {}).get("target")) or _normalize_form_text(
        (assistant_scope or {}).get("suggested_target")
    ) or "draft_review_editor_notes"
    target_label = KEEPER_ASSISTANT_TARGET_LABELS.get(target_key, "草稿审阅说明")
    flow_status_id = f"draft-flow-status-{draft_id}"
    return f"""
      <div class="assistant-source-echo">
        <p class="meta-line"><strong>本次已生成的来源回显</strong></p>
        <ul class="meta-list">
          <li>草稿归属：当前待审草稿 {escape(draft_label)}</li>
          <li>实际来源：{escape(source_context_label)}</li>
          <li>当前 review 状态：{escape(context_display['current_review_status'])}</li>
          <li>最近 editor note：{escape(context_display['recent_editor_note_label'])}</li>
          <li>最近 review 摘要：{escape(context_display['recent_review_label'])}</li>
          <li>推荐带入目标：{escape(target_label)}</li>
          <li>边界说明：仅为草稿，不会自动提交，也不会改写 authoritative state。</li>
        </ul>
        <p id="{escape(flow_status_id)}" class="helper assistant-flow-status">当前尚未带入。若采纳，将带入{escape(target_label)}，之后仍需 Keeper 人工编辑并提交。</p>
      </div>
    """


def _build_keeper_prompt_object_assistant_context(
    *,
    snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
    prompt: dict[str, Any],
) -> dict[str, Any]:
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    prompt_local_context = _build_prompt_local_context(snapshot, prompt)
    scope = _keeper_prompt_scope_metadata(
        prompt,
        local_context_summary=_normalize_form_text(prompt_local_context.get("context_summary")),
    )
    workflow = keeper_view.get("keeper_workflow") or {}
    return {
        "session": {
            "session_id": snapshot.get("session_id"),
            "scenario_title": (snapshot.get("scenario") or {}).get("title"),
            "status": snapshot.get("status"),
            "current_scene": current_scene,
            "current_beat": beat_id,
            "current_beat_title": beat_title,
        },
        "source_object": {
            "object_kind": scope["source_object_kind"],
            "object_id": scope["source_object_id"],
            "object_label": scope["source_object_label"],
        },
        "prompt": {
            "prompt_id": prompt.get("prompt_id"),
            "prompt_text": prompt.get("prompt_text"),
            "category": prompt.get("category"),
            "priority": prompt.get("priority"),
            "status": prompt.get("status"),
            "scene_id": prompt.get("scene_id"),
            "beat_id": prompt.get("beat_id"),
            "trigger_reason": prompt.get("trigger_reason"),
            "notes": list(prompt.get("notes") or [])[:4],
            "aftermath_label": prompt.get("aftermath_label"),
            "duration_rounds": prompt.get("duration_rounds"),
        },
        "prompt_local_context": {
            "current_status": prompt_local_context.get("current_status"),
            "current_category": prompt_local_context.get("current_category"),
            "latest_note": prompt_local_context.get("latest_note"),
            "recent_update": prompt_local_context.get("recent_update"),
            "context_summary": prompt_local_context.get("context_summary"),
        },
        "workflow_summary_lines": list((workflow.get("summary") or {}).get("summary_lines") or [])[:4],
        "recent_events": _event_excerpt_items(list(reversed(keeper_view.get("visible_events") or [])), limit=3),
    }


def _build_keeper_draft_object_assistant_context(
    *,
    snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    participants = _participant_map(
        [participant for participant in snapshot.get("participants") or [] if isinstance(participant, dict)]
    )
    draft_local_context = _build_draft_local_context(snapshot, draft)
    scope = _keeper_draft_scope_metadata(
        draft,
        local_context_summary=_normalize_form_text(draft_local_context.get("context_summary")),
    )
    actor_id = str(draft.get("actor_id") or "")
    return {
        "session": {
            "session_id": snapshot.get("session_id"),
            "scenario_title": (snapshot.get("scenario") or {}).get("title"),
            "status": snapshot.get("status"),
            "current_scene": current_scene,
            "current_beat": beat_id,
            "current_beat_title": beat_title,
        },
        "source_object": {
            "object_kind": scope["source_object_kind"],
            "object_id": scope["source_object_id"],
            "object_label": scope["source_object_label"],
        },
        "draft_review": {
            "draft_id": draft.get("draft_id"),
            "draft_text": draft.get("draft_text"),
            "risk_level": draft.get("risk_level"),
            "review_status": draft.get("review_status"),
            "rationale_summary": draft.get("rationale_summary"),
            "requires_explicit_approval": draft.get("requires_explicit_approval"),
            "actor_id": actor_id or None,
            "actor_name": (
                (participants.get(actor_id) or {}).get("display_name")
                if actor_id
                else None
            ),
        },
        "draft_local_context": {
            "current_review_status": draft_local_context.get("current_review_status"),
            "recent_review": draft_local_context.get("recent_review"),
            "context_summary": draft_local_context.get("context_summary"),
        },
        "recent_events": _event_excerpt_items(list(reversed(keeper_view.get("visible_events") or [])), limit=3),
    }


def _build_knowledge_assistant_context(
    *,
    source: dict[str, Any],
    preview_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    extraction = source.get("character_sheet_extraction") or {}
    return {
        "source": {
            "source_id": source.get("source_id"),
            "source_title_zh": source.get("source_title_zh"),
            "source_kind": source.get("source_kind"),
            "source_format": source.get("source_format"),
            "ingest_status": source.get("ingest_status"),
            "summary": _excerpt(
                source.get("normalized_text")
                or source.get("raw_text"),
                limit=240,
            ),
            "chunk_count": source.get("chunk_count", 0),
        },
        "preview_chunks": [
            {
                "title": chunk.get("title_zh") or chunk.get("resolved_topic") or chunk.get("topic_key"),
                "content": _excerpt(chunk.get("content") or chunk.get("text"), limit=180),
            }
            for chunk in preview_chunks[:4]
        ],
        "character_sheet_preview": (
            {
                "investigator_name": extraction.get("investigator_name"),
                "occupation": extraction.get("occupation"),
                "skills_count": len(extraction.get("skills") or {}),
                "template_profile": extraction.get("template_profile"),
            }
            if extraction
            else None
        ),
    }


def _build_recap_assistant_context(
    *,
    snapshot: dict[str, Any],
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    context = {
        "session": {
            "session_id": snapshot.get("session_id"),
            "scenario_title": (snapshot.get("scenario") or {}).get("title"),
            "status": snapshot.get("status"),
            "playtest_group": snapshot.get("playtest_group"),
            "current_scene": current_scene,
            "current_beat": beat_id,
            "current_beat_title": beat_title,
        },
        "timeline": _event_excerpt_items(list(reversed(snapshot.get("timeline") or [])), limit=8),
        "audit_log": [
            {
                "action": entry.get("action"),
                "actor_id": entry.get("actor_id"),
                "subject_id": entry.get("subject_id"),
            }
            for entry in list(reversed(snapshot.get("audit_log") or []))[:8]
        ],
        "reviewed_action_count": len(snapshot.get("reviewed_actions") or []),
        "authoritative_action_count": len(snapshot.get("authoritative_actions") or []),
    }
    if context_pack:
        context["context_pack"] = context_pack
    return context


def _build_keeper_context_pack_payload(
    *,
    service: SessionService,
    session: Any,
    keeper_view: Any,
    runtime_assistance: dict[str, Any],
    narrative_note_value: str = "",
) -> dict[str, Any]:
    return service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
        narrative_work_note=narrative_note_value,
        runtime_assistance=runtime_assistance,
    ).model_dump(mode="json")


def _render_keeper_context_pack_block(
    *,
    context_pack: dict[str, Any],
    title: str = "Keeper Context Pack",
    summary: str = "当前 keeper-side 的局势压缩层，可复用给 narrative scaffolding、recap assistant 和后续 experimental AI demo；不是 authoritative truth。",
) -> str:
    identity = context_pack.get("identity") or {}
    combat = context_pack.get("combat") or {}
    summary_lines = list(context_pack.get("summary_lines") or [])
    open_threads = list(context_pack.get("open_threads") or [])
    recent_notes = list(context_pack.get("recent_keeper_notes") or [])
    knowledge_highlights = list(context_pack.get("knowledge_highlights") or [])
    narrative_work_note = _normalize_form_text(context_pack.get("narrative_work_note"))
    disclaimer = _normalize_form_text(context_pack.get("disclaimer")) or "这是非权威的当前工作摘要。"
    summary_html = "".join(f"<li>{escape(str(line))}</li>" for line in summary_lines[:4])
    thread_html = "".join(f"<li>{escape(str(line))}</li>" for line in open_threads[:4])
    note_html = "".join(f"<li>{escape(str(line))}</li>" for line in recent_notes[:4])
    knowledge_html = "".join(f"<li>{escape(str(line))}</li>" for line in knowledge_highlights[:3])
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>{escape(title)}</h2>
            <p>{escape(summary)}</p>
          </div>
          <span class="tag">Context Pack</span>
        </div>
        <p class="helper">{escape(disclaimer)}</p>
        <ul class="meta-list">
          <li>session_id：<code>{escape(str(identity.get('session_id') or ''))}</code></li>
          <li>状态：{escape(_status_label(identity.get('status')))}</li>
          <li>当前场景：{escape(str(identity.get('current_scene') or '未知场景'))}</li>
          <li>当前 beat：<span class="mono">{escape(str(identity.get('current_beat') or '无'))}</span></li>
          <li>战斗摘要：{escape(str(combat.get('summary_line') or '当前没有战斗顺序或紧急伤势 follow-up。'))}</li>
        </ul>
        <div class="divider"></div>
        <p class="meta-line">当前局势摘要</p>
        {f'<ul class="meta-list">{summary_html}</ul>' if summary_html else '<p class="empty">当前还没有可压缩的局势摘要。</p>'}
        <p class="meta-line">未解决事项 / 当前压力</p>
        {f'<ul class="meta-list">{thread_html}</ul>' if thread_html else '<p class="empty">当前没有额外 open threads。</p>'}
        <p class="meta-line">最近 Keeper 备注</p>
        {f'<ul class="meta-list">{note_html}</ul>' if note_html else '<p class="empty">当前还没有最近 keeper 备注。</p>'}
        <p class="meta-line">资料方向 / Knowledge Hints</p>
        {f'<ul class="meta-list">{knowledge_html}</ul>' if knowledge_html else '<p class="empty">当前没有额外资料方向摘要。</p>'}
        {
            (
                f'<p class="meta-line">当前 narrative_work_note：{escape(_excerpt(narrative_work_note, limit=180) or "")}</p>'
            )
            if narrative_work_note
            else '<p class="meta-line">当前 narrative_work_note：未填写。</p>'
        }
      </section>
    """


def _generate_local_llm_assistant(
    *,
    local_llm_service: LocalLLMService,
    workspace_key: str,
    task_key: str,
    task_label: str,
    context: dict[str, Any],
) -> LocalLLMAssistantResult:
    return local_llm_service.generate_assistant(
        LocalLLMAssistantRequest(
            workspace_key=workspace_key,
            task_key=task_key,
            task_label=task_label,
            context=json.loads(json.dumps(context, ensure_ascii=False, default=str)),
        )
    )


def _exception_status_code(exc: BaseException) -> int:
    if isinstance(exc, LookupError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, PermissionError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(exc, ConflictError):
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _page_head(
    *,
    eyebrow: str,
    title: str,
    summary: str,
    actions: list[tuple[str, str, str]] | None = None,
) -> str:
    action_links = "".join(
        f'<a class="button-link {escape(kind)}" href="{escape(href, quote=True)}">{escape(label)}</a>'
        for label, href, kind in (actions or [])
    )
    toolbar = f'<div class="toolbar">{action_links}</div>' if action_links else ""
    return (
        '<section class="page-head">'
        f'<p class="eyebrow">{escape(eyebrow)}</p>'
        f"<h1>{escape(title)}</h1>"
        f"<p>{escape(summary)}</p>"
        f"{toolbar}"
        "</section>"
    )


def _app_link(
    *,
    label: str,
    href: str,
    active: bool = False,
    meta: str | None = None,
    disabled: bool = False,
) -> str:
    classes = ["nav-link"]
    if active:
        classes.append("active")
    if disabled:
        classes.append("disabled")
    meta_html = f'<span class="nav-meta">{escape(meta)}</span>' if meta else ""
    return (
        f'<a class="{" ".join(classes)}" href="{escape(href, quote=True)}">'
        f'<span class="nav-label">{escape(label)}</span>{meta_html}</a>'
    )


def _render_sidebar(
    *,
    active_section: str,
    session_snapshot: dict[str, Any] | None = None,
    active_viewer_id: str | None = None,
) -> str:
    session_id = str((session_snapshot or {}).get("session_id") or "")
    participants = [
        participant
        for participant in (session_snapshot or {}).get("participants") or []
        if isinstance(participant, dict)
    ]
    investigators = _human_investigators(
        participants,
        keeper_id=(session_snapshot or {}).get("keeper_id"),
    )
    default_investigator_id = (
        active_viewer_id
        or str((investigators[0].get("actor_id") if investigators else "") or "")
    )
    session_title = str(
        ((session_snapshot or {}).get("scenario") or {}).get("title") or "未选择会话"
    )
    current_scene, current_beat_id, current_beat_title = _scene_and_beat(session_snapshot or {})
    primary_links = [
        _app_link(
            label="Sessions",
            href="/app/sessions",
            active=active_section == "sessions",
            meta="index / group / launcher / setup",
        ),
        _app_link(
            label="Keeper",
            href=(f"/app/sessions/{session_id}/keeper" if session_id else "/app/sessions"),
            active=active_section == "keeper",
            meta="主持人工作区",
            disabled=not session_id,
        ),
        _app_link(
            label="Investigator",
            href=(
                f"/app/sessions/{session_id}/investigator/{default_investigator_id}"
                if session_id and default_investigator_id
                else "/app/sessions"
            ),
            active=active_section == "investigator",
            meta=("调查员工作区" if default_investigator_id else "先选择会话"),
            disabled=not (session_id and default_investigator_id),
        ),
        _app_link(
            label="Knowledge",
            href=(
                f"/app/knowledge?{urlencode({'session_id': session_id})}"
                if session_id
                else "/app/knowledge"
            ),
            active=active_section == "knowledge",
            meta="资料 / 模板卡 / 扫描",
        ),
        _app_link(
            label="Recap / Review",
            href=(f"/app/sessions/{session_id}/recap" if session_id else "/app/sessions"),
            active=active_section == "recap",
            meta="回顾 / 审阅 / closeout",
            disabled=not session_id,
        ),
    ]
    context_html = ""
    if session_id:
        investigator_links = "".join(
            f'<a class="context-link" href="/app/sessions/{session_id}/investigator/{escape(str(investigator.get("actor_id") or ""))}">'
            f'<span>{escape(str(investigator.get("display_name") or investigator.get("actor_id") or "调查员"))}</span>'
            f'<span class="nav-meta">{escape(str(investigator.get("actor_id") or ""))}</span></a>'
            for investigator in investigators[:4]
        )
        group_name = _group_label((session_snapshot or {}).get("playtest_group"))
        group_href = (
            f'/app/groups/{quote(str((session_snapshot or {}).get("playtest_group") or ""))}'
            if (session_snapshot or {}).get("playtest_group")
            else ""
        )
        group_value = (
            f'<a href="{escape(group_href, quote=True)}">{escape(group_name)}</a>'
            if group_href
            else escape(group_name)
        )
        context_html = f"""
          <section class="context-card">
            <h2>Current Session</h2>
            <div class="context-stack">
              <div>
                <p class="eyebrow">Session</p>
                <p><strong>{escape(session_title)}</strong></p>
                <p class="meta-line"><code>{escape(session_id)}</code></p>
              </div>
              <div class="pill-row">
                {_status_pill((session_snapshot or {}).get("status"))}
                <span class="tag">{escape(current_scene)}</span>
              </div>
              <ul class="meta-list">
                <li>当前 beat：<span class="mono">{escape(str(current_beat_id or "无"))}</span></li>
                <li>beat 标题：{escape(str(current_beat_title or "未命名节点"))}</li>
                <li>KP：{escape(str((session_snapshot or {}).get("keeper_name") or "KP"))}</li>
                <li>分组：{group_value}</li>
              </ul>
            </div>
          </section>
          <section class="context-card">
            <h2>Workspace Links</h2>
            <div class="link-stack">
              <a class="context-link" href="/app/sessions/{session_id}"><span>Session Overview</span><span class="nav-meta">launcher</span></a>
              <a class="context-link" href="/app/sessions/{session_id}/keeper"><span>Keeper Workspace</span><span class="nav-meta">主持人</span></a>
              {investigator_links or '<p class="empty">当前没有可进入的调查员工作区。</p>'}
              <a class="context-link" href="/app/sessions/{session_id}/recap"><span>Recap / Review</span><span class="nav-meta">时间线</span></a>
            </div>
          </section>
        """
    return f"""
      <section class="brand">
        <p class="brand-kicker">Call of Cthulhu 7e</p>
        <h1>Web GUI MVP</h1>
        <p>先把 sessions / keeper / investigator / knowledge / recap 收成统一工作壳，详细动作继续渐进接到现有主链。</p>
      </section>
      <section class="nav-group">
        <h2>Navigation</h2>
        <div class="nav-stack">
          {"".join(primary_links)}
        </div>
      </section>
      {context_html}
    """


def _character_snapshot(
    participant: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> str:
    participant = participant or {}
    state = state or {}
    character = participant.get("character") or {}
    hp_max = character.get("max_hit_points")
    mp_max = character.get("max_magic_points")
    status_tags: list[str] = []
    if state.get("heavy_wound_active"):
        status_tags.append('<span class="tag warn">重伤</span>')
    if state.get("is_dying"):
        status_tags.append('<span class="tag danger">濒死</span>')
    if state.get("is_stable"):
        status_tags.append('<span class="tag success">已稳定</span>')
    if state.get("rescue_window_open"):
        status_tags.append('<span class="tag warn">短时可救</span>')
    if state.get("death_confirmed"):
        status_tags.append('<span class="tag danger">已死亡</span>')
    if not status_tags:
        status_tags.append('<span class="tag success">状态平稳</span>')
    return f"""
      <div class="metric-grid">
        <article class="metric">
          <p class="metric-label">HP</p>
          <strong>{escape(str(state.get('current_hit_points', '—')))}</strong>
          <span>/ {escape(str(hp_max if hp_max is not None else '—'))}</span>
        </article>
        <article class="metric">
          <p class="metric-label">SAN</p>
          <strong>{escape(str(state.get('current_sanity', '—')))}</strong>
          <span>当前理智</span>
        </article>
        <article class="metric">
          <p class="metric-label">MP</p>
          <strong>{escape(str(state.get('current_magic_points', '—')))}</strong>
          <span>/ {escape(str(mp_max if mp_max is not None else '—'))}</span>
        </article>
      </div>
      <div class="pill-row">{''.join(status_tags)}</div>
    """


def _render_session_card(session: dict[str, Any]) -> str:
    session_id = str(session.get("session_id") or "")
    scenario = session.get("scenario") or {}
    participants = [
        participant
        for participant in session.get("participants") or []
        if isinstance(participant, dict)
    ]
    investigators = _human_investigators(participants, keeper_id=session.get("keeper_id"))
    first_investigator_id = str((investigators[0].get("actor_id") if investigators else "") or "")
    current_scene, beat_id, beat_title = _scene_and_beat(session)
    progress_state = session.get("progress_state") or {}
    active_prompt_count = len(progress_state.get("queued_kp_prompts") or [])
    active_objective_count = len(progress_state.get("active_scene_objectives") or [])
    combat_context = session.get("combat_context") or {}
    current_actor = combat_context.get("current_actor_id")
    return f"""
      <article class="list-card">
        <div class="list-head">
          <h3>{escape(str(scenario.get('title') or '未命名会话'))}</h3>
          {_status_pill(session.get('status'))}
        </div>
        <p>{escape(_excerpt((session.get('current_scene') or {}).get('summary') or scenario.get('hook') or ''))}</p>
        <ul class="meta-list">
          <li>session_id：<code>{escape(session_id)}</code></li>
          <li>分组：{escape(_group_label(session.get('playtest_group')))}</li>
          <li>当前场景：{escape(current_scene)}</li>
          <li>当前 beat：<span class="mono">{escape(str(beat_id or '无'))}</span> / {escape(str(beat_title or '未命名节点'))}</li>
          <li>调查员：{escape(str(len(investigators)))}</li>
          <li>待处理提示 / 目标：{escape(str(active_prompt_count))} / {escape(str(active_objective_count))}</li>
          <li>战斗上下文：{escape(str(current_actor or '未建立'))}</li>
        </ul>
        <div class="toolbar">
          <a class="button-link ghost" href="/app/sessions/{escape(session_id)}">总览</a>
          <a class="button-link secondary" href="/app/sessions/{escape(session_id)}/keeper">Keeper</a>
          {
              f'<a class="button-link ghost" href="/app/sessions/{escape(session_id)}/investigator/{escape(first_investigator_id)}">Investigator</a>'
              if first_investigator_id
              else '<span class="button-link ghost">暂无调查员</span>'
          }
          <a class="button-link ghost" href="/app/sessions/{escape(session_id)}/recap">Recap</a>
        </div>
      </article>
    """


def _render_setup_page(
    *,
    form_values: dict[str, Any] | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    values = form_values or _default_playtest_setup_form_values()
    selected_template = str(values.get("scenario_template") or "whispering_guesthouse")
    selected_template_meta = _get_playtest_scenario_template(
        selected_template
    ) or _playtest_scenario_templates()[0]
    group_name = _normalize_form_text(str(values.get("playtest_group") or ""))
    actions: list[tuple[str, str, str]] = [
        ("返回 Sessions", "/app/sessions", "ghost"),
        ("准备资料", "/app/knowledge", "secondary"),
        ("旧版 Create", "/playtest/sessions/create", "ghost"),
    ]
    if group_name:
        actions.insert(1, ("返回当前分组", f"/app/groups/{quote(group_name)}", "ghost"))
    template_cards = "".join(
        f"""
        <label class="radio-card">
          <input
            type="radio"
            name="scenario_template"
            value="{escape(str(template['template_id']))}"
            {"checked" if template['template_id'] == selected_template else ""}
          />
          <div>
            <strong>{escape(str(template['title']))}</strong>
            <p>{escape(str(template['summary']))}</p>
            <p class="meta-line">
              {escape(str(template['experience_hint']))}
              · {escape(str(template['recommended_party']))}
            </p>
          </div>
        </label>
        """
        for template in _playtest_scenario_templates()
    )
    investigator_names = list(values.get("investigator_names") or ["", "", "", ""])
    investigator_inputs = "".join(
        f"""
        <label>
          调查员 {index}
          <input
            type="text"
            name="investigator_{index}_name"
            value="{escape(str(investigator_names[index - 1] or ''))}"
            {"required" if index == 1 else ""}
          />
        </label>
        """
        for index in range(1, 5)
    )
    body = (
        _page_head(
            eyebrow="Setup / Create",
            title="在 App Shell 内创建新局",
            summary="create / setup 已收进 Web GUI 壳，仍复用现有 playtest 模板和 start_session 语义，不新开后端产品线。",
            actions=actions,
        )
        + _detail_block(detail)
        + f"""
        <section class="surface">
          <div class="surface-header">
            <div>
              <h2>最小 setup</h2>
              <p>保持现有模板建局语义，只把入口、层级和回链收进统一 app shell。</p>
            </div>
          </div>
          <form method="post" action="/app/setup" class="form-stack">
            <div class="field-grid">
              <label>
                Keeper 名称
                <input
                  type="text"
                  name="keeper_name"
                  value="{escape(str(values.get('keeper_name') or ''))}"
                  required
                />
              </label>
              <label>
                分组（可选）
                <input
                  type="text"
                  name="playtest_group"
                  value="{escape(str(values.get('playtest_group') or ''))}"
                  placeholder="例如：旅店线压力测试"
                />
              </label>
            </div>
            <fieldset>
              <legend>Scenario Template</legend>
              <div class="radio-grid">{template_cards}</div>
            </fieldset>
            <article class="list-card">
              <div class="list-head">
                <h3>当前选择</h3>
                <span class="tag">{escape(str(selected_template_meta['template_id']))}</span>
              </div>
              <p><strong>{escape(str(selected_template_meta['title']))}</strong></p>
              <p>{escape(str(selected_template_meta['summary']))}</p>
              <p class="meta-line">
                {escape(str(selected_template_meta['experience_hint']))}
                · {escape(str(selected_template_meta['recommended_party']))}
              </p>
            </article>
            <div class="field-grid">
              {investigator_inputs}
            </div>
            <p class="helper">至少填写 1 名调查员。创建成功后直接进入新的 session overview，而不是跳回旧 launcher。</p>
            <button class="button-button" type="submit">创建并进入 App Shell</button>
          </form>
        </section>
        """
    )
    return render_web_app_shell(
        title="App Setup",
        sidebar_html=_render_sidebar(active_section="sessions"),
        body_html=body,
        status_code=status_code,
    )


def _render_sessions_page(*, sessions: list[dict[str, Any]]) -> HTMLResponse:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for session in sessions:
        label = _group_label(session.get("playtest_group"))
        if label not in grouped:
            grouped[label] = []
            order.append(label)
        grouped[label].append(session)
    if sessions:
        sections = "".join(
            f"""
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>分组：{escape(group_name)}</h2>
                  <p>统一承接 session index / group / launcher 的入口层。</p>
                </div>
                {
                    f'<a class="button-link ghost" href="/app/groups/{quote(group_name)}">进入分组页</a>'
                    if group_name != "未分组"
                    else ''
                }
              </div>
              <div class="card-list">
                {"".join(_render_session_card(session) for session in grouped[group_name])}
              </div>
            </section>
            """
            for group_name in order
        )
    else:
        sections = (
            '<section class="surface"><h2>Session 列表</h2>'
            '<p class="empty">当前还没有 session。先创建一局，再从这里进入新的 Web GUI 壳。</p>'
            "</section>"
        )
    body = (
        _page_head(
            eyebrow="Sessions",
            title="Session Workspace Index",
            summary="现有 launcher / group / recap / keeper / investigator 已经具备后端语义，这里把它们收成统一入口，并把 create flow 收进 app shell。",
            actions=[
                ("创建新局", "/app/setup", ""),
                ("准备资料", "/app/knowledge", "secondary"),
                ("旧版 Session 列表", "/playtest/sessions", "ghost"),
            ],
        )
        + f"""
        <section class="surface">
          <h2>本轮范围</h2>
          <div class="metric-grid">
            <article class="metric">
              <p class="metric-label">Sessions</p>
              <strong>{escape(str(len(sessions)))}</strong>
              <span>已发现的会话</span>
            </article>
            <article class="metric">
              <p class="metric-label">Groups</p>
              <strong>{escape(str(len(order)))}</strong>
              <span>当前分组桶</span>
            </article>
            <article class="metric">
              <p class="metric-label">Primary UX</p>
              <strong>Keeper / Investigator</strong>
              <span>优先做工作区，而不是继续补底层</span>
            </article>
          </div>
        </section>
        {sections}
        """
    )
    return render_web_app_shell(
        title="Web GUI Sessions",
        sidebar_html=_render_sidebar(active_section="sessions"),
        body_html=body,
    )


def _render_group_page(*, group_name: str, sessions: list[dict[str, Any]]) -> HTMLResponse:
    create_href = f"/app/setup?{urlencode({'playtest_group': group_name})}"
    body = (
        _page_head(
            eyebrow="Group",
            title=f"分组：{group_name}",
            summary="group page 不再只是散链接，而是作为 session workspace 的一个过滤视图。",
            actions=[
                ("继续在本组开局", create_href, ""),
                ("返回 Sessions", "/app/sessions", "ghost"),
                ("旧版分组页", f"/playtest/groups/{quote(group_name)}", "ghost"),
            ],
        )
        + (
            f"""
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>本组 session</h2>
                  <p>同一轮测试、同一主题实验或同一 Keeper 批次都可以落在这里。</p>
                </div>
                <span class="tag">{escape(str(len(sessions)))} 个 session</span>
              </div>
              <div class="card-list">
                {"".join(_render_session_card(session) for session in sessions)}
              </div>
            </section>
            """
            if sessions
            else '<section class="surface"><h2>本组 session</h2><p class="empty">当前分组下还没有 session。</p></section>'
        )
    )
    return render_web_app_shell(
        title=f"Group {group_name}",
        sidebar_html=_render_sidebar(active_section="sessions"),
        body_html=body,
    )


def _render_event_cards(
    events: list[dict[str, Any]],
    *,
    empty_text: str,
    limit: int = 6,
) -> str:
    items = events[:limit]
    if not items:
        return f'<p class="empty">{escape(empty_text)}</p>'
    return "".join(
        f"""
        <article class="list-card">
          <div class="list-head">
            <h3>{escape(str(item.get('text') or item.get('event_type') or '事件'))}</h3>
            <span class="list-meta">{escape(str(item.get('created_at') or ''))}</span>
          </div>
          <p class="meta-line">event_type：{escape(str(item.get('event_type') or 'unknown'))}</p>
        </article>
        """
        for item in items
    )


def _render_audit_cards(
    entries: list[dict[str, Any]],
    *,
    empty_text: str,
    limit: int = 6,
) -> str:
    items = list(reversed(entries))[:limit]
    if not items:
        return f'<p class="empty">{escape(empty_text)}</p>'
    return "".join(
        f"""
        <article class="list-card">
          <div class="list-head">
            <h3>{escape(str(entry.get('action') or 'audit'))}</h3>
            <span class="list-meta">{escape(str(entry.get('created_at') or ''))}</span>
          </div>
          <p class="meta-line">subject_id：{escape(str(entry.get('subject_id') or '—'))}</p>
          <p class="meta-line">actor_id：{escape(str(entry.get('actor_id') or '—'))}</p>
        </article>
        """
        for entry in items
    )


def _render_session_overview_page(*, session_id: str, snapshot: dict[str, Any]) -> HTMLResponse:
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    participant_by_id = _participant_map(participants)
    character_states = {
        str(actor_id): state
        for actor_id, state in (snapshot.get("character_states") or {}).items()
        if isinstance(state, dict)
    }
    investigators = _human_investigators(participants, keeper_id=snapshot.get("keeper_id"))
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    progress_state = snapshot.get("progress_state") or {}
    combat_context = snapshot.get("combat_context") or {}
    current_actor = combat_context.get("current_actor_id")
    clues = snapshot.get("scenario", {}).get("clues") or []
    shared_clues = [
        clue
        for clue in clues
        if isinstance(clue, dict) and clue.get("status") == "shared_with_party"
    ]
    if investigators:
        investigator_cards = "".join(
            f"""
            <article class="list-card">
              <div class="list-head">
                <h3>{escape(str(investigator.get('display_name') or investigator.get('actor_id') or '调查员'))}</h3>
                <a class="button-link ghost" href="/app/sessions/{escape(session_id)}/investigator/{escape(str(investigator.get('actor_id') or ''))}">打开工作区</a>
              </div>
              {_character_snapshot(
                  participant_by_id.get(str(investigator.get('actor_id') or '')),
                  character_states.get(str(investigator.get('actor_id') or '')),
              )}
            </article>
            """
            for investigator in investigators
        )
    else:
        investigator_cards = '<p class="empty">当前没有可进入的调查员工作区。</p>'
    body = (
        _page_head(
            eyebrow="Session Overview",
            title=str((snapshot.get("scenario") or {}).get("title") or "未命名会话"),
            summary="这里承接旧 launcher 语义，但把 keeper / investigator / knowledge / recap 收进一套统一导航和上下文。",
            actions=[
                ("Keeper Workspace", f"/app/sessions/{session_id}/keeper", ""),
                ("Knowledge", f"/app/knowledge?{urlencode({'session_id': session_id})}", "secondary"),
                ("Legacy Launcher", f"/playtest/sessions/{session_id}/home", "ghost"),
            ],
        )
        + f"""
        <section class="surface">
          <div class="metric-grid">
            <article class="metric">
              <p class="metric-label">状态</p>
              <strong>{escape(_status_label(snapshot.get('status')))}</strong>
              <span>{escape(str(snapshot.get('status') or ''))}</span>
            </article>
            <article class="metric">
              <p class="metric-label">场景 / Beat</p>
              <strong>{escape(current_scene)}</strong>
              <span>{escape(str(beat_id or '无'))} / {escape(str(beat_title or '未命名节点'))}</span>
            </article>
            <article class="metric">
              <p class="metric-label">待处理项</p>
              <strong>{escape(str(len(progress_state.get('queued_kp_prompts') or [])))}</strong>
              <span>KP prompts</span>
            </article>
            <article class="metric">
              <p class="metric-label">战斗</p>
              <strong>{escape(str(current_actor or '未建立'))}</strong>
              <span>{escape(str(combat_context.get('round_number') or '—'))} 回合</span>
            </article>
            <article class="metric">
              <p class="metric-label">共享线索</p>
              <strong>{escape(str(len(shared_clues)))}</strong>
              <span>party-visible</span>
            </article>
            <article class="metric">
              <p class="metric-label">State Version</p>
              <strong>{escape(str(snapshot.get('state_version') or '—'))}</strong>
              <span>当前 authoritative 状态</span>
            </article>
          </div>
        </section>
        <section class="content-grid">
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>Workspace 入口</h2>
                  <p>先让操作入口稳定收口，再逐步把细节表单迁进新壳。</p>
                </div>
              </div>
              <div class="surface-grid">
                <article class="list-card">
                  <h3>Keeper</h3>
                  <p>局势摘要、待处理提示、目标推进、战斗与伤势 follow-up。</p>
                  <div class="toolbar">
                    <a class="button-link secondary" href="/app/sessions/{escape(session_id)}/keeper">打开 Keeper Workspace</a>
                    <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper">旧版详细页</a>
                  </div>
                </article>
                <article class="list-card">
                  <h3>Knowledge</h3>
                  <p>资料、模板卡、scenario scan 保持为独立 workspace，再从这里回到 session。</p>
                  <div class="toolbar">
                    <a class="button-link secondary" href="/app/knowledge?{escape(urlencode({'session_id': session_id}), quote=True)}">打开 Knowledge</a>
                    <a class="button-link ghost" href="/playtest/knowledge">旧版知识页</a>
                  </div>
                </article>
                <article class="list-card">
                  <h3>Recap / Review</h3>
                  <p>把时间线、audit 和 closeout 汇到同一页，不再作为孤立尾页。</p>
                  <div class="toolbar">
                    <a class="button-link secondary" href="/app/sessions/{escape(session_id)}/recap">打开 Recap</a>
                    <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/recap">旧版 recap</a>
                  </div>
                </article>
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>调查员入口</h2>
                  <p>调查员工作区继续严格按 viewer 过滤，不共享 keeper-only 信息。</p>
                </div>
                <span class="tag">{escape(str(len(investigators)))} 名调查员</span>
              </div>
              <div class="card-list">
                {investigator_cards}
              </div>
            </section>
          </div>
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>当前局面</h2>
                  <p>旧 launcher 的摘要逻辑在这里常驻。</p>
                </div>
              </div>
              <ul class="meta-list">
                <li>session_id：<code>{escape(session_id)}</code></li>
                <li>KP：{escape(str(snapshot.get('keeper_name') or 'KP'))}</li>
                <li>分组：{escape(_group_label(snapshot.get('playtest_group')))}</li>
                <li>当前场景：{escape(current_scene)}</li>
                <li>当前 beat：<span class="mono">{escape(str(beat_id or '无'))}</span></li>
                <li>beat 标题：{escape(str(beat_title or '未命名节点'))}</li>
                <li>当前行动者：{escape(str(current_actor or '无'))}</li>
              </ul>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>最近推进</h2>
                  <p>把旧 launcher / recap 的关键信息压成当前入口摘要。</p>
                </div>
              </div>
              <div class="card-list">
                {_render_event_cards(list(reversed(snapshot.get('timeline') or [])), empty_text='当前还没有可展示的推进记录。', limit=5)}
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>会话工具</h2>
                  <p>checkpoint / export 先保留旧页面或既有 JSON。</p>
                </div>
              </div>
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}">Checkpoint 页</a>
                <a class="button-link ghost" href="/sessions/{escape(session_id)}/snapshot">Snapshot JSON</a>
                <a class="button-link ghost" href="/sessions/{escape(session_id)}/export">Keeper Export JSON</a>
              </div>
            </section>
          </div>
        </section>
        """
    )
    return render_web_app_shell(
        title=f"Session {session_id} Overview",
        sidebar_html=_render_sidebar(active_section="sessions", session_snapshot=snapshot),
        body_html=body,
    )


def _render_prompt_cards(
    prompts: list[dict[str, Any]],
    *,
    snapshot: dict[str, Any],
    session_id: str,
    operator_id: str,
    completion_notices: dict[str, str] | None = None,
    assistant_scope: dict[str, str] | None = None,
    assistant_adoption: dict[str, str] | None = None,
) -> str:
    if not prompts:
        return '<p class="empty">当前没有待处理提示。</p>'
    cards: list[str] = []
    for prompt in prompts[:4]:
        prompt_id = str(prompt.get("prompt_id") or "prompt")
        note_target_id = f"prompt-note-{prompt_id}"
        adoption_status_id = f"prompt-note-status-{prompt_id}"
        flow_status_id = f"prompt-flow-status-{prompt_id}"
        current_notes = prompt.get("notes") or []
        notes_html = "".join(f"<li>{escape(str(note))}</li>" for note in current_notes[:3])
        prompt_local_context = _build_prompt_local_context(snapshot, prompt)
        cards.append(
            f"""
            <article class="list-card">
              <div class="list-head">
                <h3>{escape(str(prompt.get('prompt_text') or '未命名提示'))}</h3>
                <span class="tag">{escape(str(prompt.get('status') or 'pending'))}</span>
              </div>
              <p>{escape(str(prompt.get('trigger_reason') or prompt.get('category') or 'kp_prompt'))}</p>
                <div class="divider"></div>
              <p class="meta-line">当前备注</p>
              {f'<ul class="meta-list">{notes_html}</ul>' if notes_html else '<p class="empty">当前还没有 keeper 备注。</p>'}
              {_render_prompt_generation_preview(prompt, local_context=prompt_local_context)}
              <form method="post" action="/app/sessions/{escape(session_id)}/keeper/prompts/{escape(prompt_id)}/assistant">
                <button class="button-button secondary" type="submit">为这条 Prompt 生成备注草稿</button>
              </form>
              {_render_prompt_generation_source_echo(
                  prompt,
                  local_context=prompt_local_context,
                  assistant_scope=assistant_scope,
                  assistant_adoption=assistant_adoption,
                  prompt_id=prompt_id,
              )}
              <form method="post" action="/app/sessions/{escape(session_id)}/keeper/prompts/{escape(prompt_id)}/status">
                <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                <label>
                  备注（可选）
                  <textarea id="{escape(note_target_id)}" name="note" rows="2" placeholder="可选。顺手留一句处理说明。"></textarea>
                </label>
                {_render_assistant_adopt_button(
                    assistant_adoption=assistant_adoption,
                    target_kind='prompt_note',
                    target_id=note_target_id,
                    status_id=adoption_status_id,
                    flow_status_id=flow_status_id,
                    flow_status_text='该草稿来自当前 Prompt 的 assistant 生成。已带入：Prompt 备注框。当前仍待 Keeper 人工编辑并提交。',
                    source_object_kind='prompt',
                    source_object_id=prompt_id,
                )}
                {
                    (
                        f'<p id="{escape(adoption_status_id)}" class="helper adoption-status">当前可采纳：'
                        f"{escape(assistant_adoption['draft_kind_label'])}。来源："
                        f"{escape(assistant_adoption['source_context_label'])} 目标：当前 Prompt 备注框。"
                        ' 只会带入文本，不会自动提交。</p>'
                    )
                    if _assistant_targets_current_object(
                        assistant_adoption,
                        target_kind="prompt_note",
                        source_object_kind="prompt",
                        source_object_id=prompt_id,
                    )
                    else ""
                }
                <div class="inline-form-grid">
                  <button class="button-button ghost" type="submit" name="status" value="acknowledged">标记 acknowledged</button>
                  <button class="button-button secondary" type="submit" name="status" value="completed">标记 completed</button>
                  <button class="button-button danger" type="submit" name="status" value="dismissed">标记 dismissed</button>
                </div>
              </form>
              {
                  (
                      f'<p class="helper assistant-completion-status">{escape((completion_notices or {}).get(prompt_id) or "")}</p>'
                  )
                  if (completion_notices or {}).get(prompt_id)
                  else ""
              }
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#prompt-targets">旧版提示处理区</a>
              </div>
            </article>
            """
        )
    return "".join(cards)


def _render_draft_cards(
    drafts: list[dict[str, Any]],
    *,
    snapshot: dict[str, Any],
    session_id: str,
    reviewer_id: str,
    completion_notices: dict[str, str] | None = None,
    assistant_scope: dict[str, str] | None = None,
    assistant_adoption: dict[str, str] | None = None,
) -> str:
    if not drafts:
        return '<p class="empty">当前没有待审草稿。</p>'
    cards: list[str] = []
    for draft in drafts[:4]:
        draft_id = str(draft.get("draft_id") or "draft")
        note_target_id = f"draft-review-note-{draft_id}"
        adoption_status_id = f"draft-review-status-{draft_id}"
        flow_status_id = f"draft-flow-status-{draft_id}"
        draft_local_context = _build_draft_local_context(snapshot, draft)
        cards.append(
            f"""
            <article class="list-card">
              <div class="list-head">
                <h3>{escape(_excerpt(draft.get('draft_text'), limit=56) or '未命名草稿')}</h3>
                <span class="tag warn">{escape(str(draft.get('risk_level') or 'low'))}</span>
              </div>
              <p>{escape(_excerpt(draft.get('rationale_summary') or '待人工审核'))}</p>
              {_render_draft_generation_preview(draft, local_context=draft_local_context)}
              <form method="post" action="/app/sessions/{escape(session_id)}/draft-actions/{escape(draft_id)}/assistant">
                <button class="button-button secondary" type="submit">为这条草稿生成审阅说明</button>
              </form>
              {_render_draft_generation_source_echo(
                  draft,
                  local_context=draft_local_context,
                  assistant_scope=assistant_scope,
                  assistant_adoption=assistant_adoption,
                  draft_id=draft_id,
              )}
              <form method="post" action="/app/sessions/{escape(session_id)}/draft-actions/{escape(draft_id)}/review">
                <input type="hidden" name="reviewer_id" value="{escape(reviewer_id)}" />
                <label>
                  editor_notes（可选）
                  <textarea id="{escape(note_target_id)}" name="editor_notes" rows="2" placeholder="可选。顺手留一句审阅说明。"></textarea>
                </label>
                {_render_assistant_adopt_button(
                    assistant_adoption=assistant_adoption,
                    target_kind='draft_review_editor_notes',
                    target_id=note_target_id,
                    status_id=adoption_status_id,
                    flow_status_id=flow_status_id,
                    flow_status_text='该草稿来自当前待审草稿的 assistant 生成。已带入：草稿审阅说明框。当前仍待 Keeper 人工编辑并提交。',
                    source_object_kind='draft',
                    source_object_id=draft_id,
                )}
                {
                    (
                        f'<p id="{escape(adoption_status_id)}" class="helper adoption-status">当前可采纳：'
                        f"{escape(assistant_adoption['draft_kind_label'])}。来源："
                        f"{escape(assistant_adoption['source_context_label'])} 目标：当前草稿审阅说明框。"
                        ' 只会带入文本，不会自动提交。</p>'
                    )
                    if _assistant_targets_current_object(
                        assistant_adoption,
                        target_kind="draft_review_editor_notes",
                        source_object_kind="draft",
                        source_object_id=draft_id,
                    )
                    else ""
                }
                <div class="inline-form-grid">
                  <button class="button-button secondary" type="submit" name="decision" value="approve">批准草稿</button>
                  <button class="button-button danger" type="submit" name="decision" value="reject">驳回草稿</button>
                </div>
              </form>
              {
                  (
                      f'<p class="helper assistant-completion-status">{escape((completion_notices or {}).get(draft_id) or "")}</p>'
                  )
                  if (completion_notices or {}).get(draft_id)
                  else ""
              }
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#draft-review-targets">旧版草稿审阅区</a>
              </div>
            </article>
            """
        )
    return "".join(cards)


def _render_wound_cards(
    *,
    character_states: dict[str, dict[str, Any]],
    participant_by_id: dict[str, dict[str, Any]],
    session_id: str,
    operator_id: str,
) -> str:
    rows: list[str] = []
    for actor_id, state in character_states.items():
        if not any(
            [
                state.get("heavy_wound_active"),
                state.get("is_unconscious"),
                state.get("is_dying"),
                state.get("death_confirmed"),
                state.get("rescue_window_open"),
            ]
        ):
            continue
        name = str((participant_by_id.get(actor_id) or {}).get("display_name") or actor_id)
        flags = []
        if state.get("heavy_wound_active"):
            flags.append("重伤")
        if state.get("is_unconscious"):
            flags.append("昏迷")
        if state.get("is_dying"):
            flags.append("濒死")
        if state.get("is_stable"):
            flags.append("已稳定")
        if state.get("rescue_window_open"):
            flags.append("短时可救")
        if state.get("death_confirmed"):
            flags.append("已死亡")
        action_toolbar = (
            f"""
              <div class="inline-form-grid">
                <form method="post" action="/app/sessions/{escape(session_id)}/keeper/wounds/{escape(actor_id)}/resolve">
                  <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                  <input type="hidden" name="resolution" value="{escape(KeeperWoundResolution.KEEP_RESCUE_WINDOW_OPEN.value)}" />
                  <button class="button-button ghost" type="submit">保留抢救窗口</button>
                </form>
                <form method="post" action="/app/sessions/{escape(session_id)}/keeper/wounds/{escape(actor_id)}/resolve">
                  <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                  <input type="hidden" name="resolution" value="{escape(KeeperWoundResolution.STABILIZE_UNCONSCIOUS.value)}" />
                  <button class="button-button secondary" type="submit">判定为稳定昏迷</button>
                </form>
                <form method="post" action="/app/sessions/{escape(session_id)}/keeper/wounds/{escape(actor_id)}/resolve">
                  <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                  <input type="hidden" name="resolution" value="{escape(KeeperWoundResolution.CONFIRM_DEATH.value)}" />
                  <button class="button-button danger" type="submit">确认死亡</button>
                </form>
              </div>
            """
            if not state.get("death_confirmed")
            else '<p class="meta-line">当前已确认死亡，不再提供进一步伤势裁定按钮。</p>'
        )
        rows.append(
            f"""
            <article class="list-card">
              <div class="list-head">
                <h3>{escape(name)}</h3>
                <span class="tag danger">{escape(str(state.get('current_hit_points', '—')))} HP</span>
              </div>
              <p>{escape(' / '.join(flags) or '需要人工关注')}</p>
              {action_toolbar}
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#wound-follow-up">旧版伤势 follow-up</a>
              </div>
            </article>
            """
        )
    return "".join(rows) if rows else '<p class="empty">当前没有需要 KP 处理的伤势 follow-up。</p>'


def _render_runtime_assistance(runtime_assistance: dict[str, Any], *, session_id: str) -> str:
    rule_hints = runtime_assistance.get("rule_hints") or []
    knowledge_hints = runtime_assistance.get("knowledge_hints") or []
    if not rule_hints and not knowledge_hints:
        return (
            '<p class="empty">当前没有额外规则或知识提示。</p>'
            f'<div class="toolbar"><a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/rules">旧版规则查询</a></div>'
        )
    cards = "".join(
        f"""
        <article class="list-card">
          <div class="list-head">
            <h3>{escape(str(item.get('title') or item.get('title_zh') or item.get('topic_key') or '提示'))}</h3>
            <span class="list-meta">{escape(str(item.get('source') or item.get('source_id') or 'hint'))}</span>
          </div>
          <p>{escape(_excerpt(item.get('summary') or item.get('content') or item.get('text'), limit=150))}</p>
        </article>
        """
        for item in [*rule_hints[:2], *knowledge_hints[:2]]
    )
    return f"""
      <div class="card-list">
        {cards}
        <div class="toolbar">
          <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/rules">旧版规则查询</a>
          <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#hook-materials">旧版 hook 材料</a>
        </div>
      </div>
    """


def _render_keeper_narrative_scaffolding(
    *,
    session_id: str,
    snapshot: dict[str, Any],
    narrative_note_value: str = "",
    narrative_completion_notice: str | None = None,
    narrative_result: LocalLLMAssistantResult | None = None,
    narrative_scope: dict[str, str] | None = None,
    selected_narrative_task: str | None = None,
) -> str:
    narrative_note_target_id = f"narrative-work-note-{session_id}"
    narrative_note_status_id = f"narrative-work-note-status-{session_id}"
    narrative_note_flow_status_id = f"narrative-work-note-flow-status-{session_id}"
    narrative_scope = narrative_scope or _keeper_narrative_scope_metadata(
        session_id=session_id,
        snapshot=snapshot,
    )
    narrative_adoption = _keeper_narrative_assistant_adoption(
        narrative_result,
        assistant_scope=narrative_scope,
    )
    adoption_matches_target = _assistant_targets_current_object(
        narrative_adoption,
        target_kind="narrative_work_note",
        source_object_kind="keeper_session",
        source_object_id=session_id,
    )
    adopted_status_text = ""
    if narrative_adoption is not None and adoption_matches_target:
        adopted_status_text = (
            f"已带入 {narrative_adoption['draft_kind_label']}。来源："
            f"{narrative_adoption['source_context_label']} 当前仍需 Keeper 人工编辑并提交。"
        )
    return f"""
      {_render_local_llm_assistant_panel(
          title="AI-KP Narrative Scaffolding",
          description="可选的本地 LLM 剧情支架块，只基于 keeper 可见摘要生成下一幕开场、线索/下一拍或 NPC 反应建议。",
          action=f"/app/sessions/{quote(session_id)}/keeper/narrative-assistant",
          tasks=KEEPER_NARRATIVE_TASKS,
          selected_task=selected_narrative_task,
          result=narrative_result,
          hidden_fields={"narrative_note": narrative_note_value},
          extra_output_html=_render_assistant_draft_source(
              assistant_scope=narrative_scope,
              assistant_adoption=narrative_adoption,
          ),
      )}
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>当前剧情工作备注</h2>
            <p>只用于 keeper 当前页组织 scene framing / beat / NPC 反应思路，不会写入 session truth。</p>
          </div>
        </div>
        <form method="post" action="/app/sessions/{escape(session_id)}/keeper/narrative-note" class="form-stack">
          <label>
            剧情工作备注 / 场景支架
            <textarea id="{escape(narrative_note_target_id)}" name="narrative_note" rows="8" placeholder="可把 narrative scaffolding 建议先整理在这里，再人工确认。">{escape(narrative_note_value)}</textarea>
          </label>
          {_render_assistant_adopt_button(
              assistant_adoption=narrative_adoption,
              target_kind="narrative_work_note",
              target_id=narrative_note_target_id,
              status_id=narrative_note_status_id,
              status_text=adopted_status_text or None,
              flow_status_id=narrative_note_flow_status_id,
              flow_status_text="该草稿来自当前 keeper narrative scaffolding。已带入：当前剧情工作备注框。当前仍待 Keeper 人工编辑并提交。",
              source_object_kind="keeper_session",
              source_object_id=session_id,
          )}
          {
              (
                  f'<p id="{escape(narrative_note_status_id)}" class="helper adoption-status">当前可采纳：'
                  f"{escape(narrative_adoption['draft_kind_label'])}。来源："
                  f"{escape(narrative_adoption['source_context_label'])} 目标：当前剧情工作备注框。"
                  ' 只会带入文本，不会自动提交。</p>'
              )
              if adoption_matches_target
              else ""
          }
          {
              (
                  f'<p id="{escape(narrative_note_flow_status_id)}" class="helper assistant-flow-status">'
                  '当前尚未带入。若采纳，将带入当前剧情工作备注框，之后仍需 Keeper 人工编辑并提交。</p>'
              )
              if adoption_matches_target
              else ""
          }
          <button class="button-button secondary" type="submit">确认当前剧情工作备注</button>
        </form>
        {
            (
                f'<p class="helper assistant-completion-status">{escape(narrative_completion_notice)}</p>'
            )
            if narrative_completion_notice
            else ""
        }
      </section>
    """


def _render_keeper_operation_result(action_result: dict[str, Any] | None) -> str:
    if not action_result:
        return ""
    kind = str(action_result.get("kind") or "")
    payload = action_result.get("payload") or {}
    if kind == "lifecycle":
        lines = [
            str(payload.get("message") or ""),
            f"state_version：{payload.get('state_version')}",
        ]
        return _render_feedback_panel(title="会话生命周期已更新", lines=lines)
    if kind in {"combat_start", "combat_advance"}:
        combat_context = payload.get("combat_context") or {}
        lines = [
            str(payload.get("message") or ""),
            f"当前行动者：{combat_context.get('current_actor_id') or '未建立'}",
            f"回合：{combat_context.get('round_number') or '—'}",
            f"顺序人数：{len(combat_context.get('turn_order') or [])}",
        ]
        return _render_feedback_panel(
            title="战斗流程已更新",
            lines=lines,
        )
    if kind == "wound_resolution":
        lines = [
            str(payload.get("message") or ""),
            f"actor_id：{payload.get('actor_id') or '—'}",
            f"稳定：{_bool_label(payload.get('is_stable'))}",
            f"濒死：{_bool_label(payload.get('is_dying'))}",
            f"短时抢救窗口：{_rescue_window_label(payload)}",
            f"死亡确认：{_bool_label(payload.get('death_confirmed'))}",
        ]
        return _render_feedback_panel(title="伤势后续已裁定", lines=lines)
    if kind == "prompt_status":
        lines = [
            str(payload.get("message") or ""),
            f"prompt_id：{((payload.get('prompt') or {}).get('prompt_id') or '—')}",
            f"状态：{((payload.get('prompt') or {}).get('status') or '—')}",
        ]
        note = str(action_result.get("note") or "").strip()
        if note:
            lines.append(f"备注：{note}")
        return _render_feedback_panel(title="Keeper Prompt 已更新", lines=lines)
    if kind == "draft_review":
        lines = [
            str(payload.get("message") or ""),
            f"grounding_degraded：{_bool_label(payload.get('grounding_degraded'))}",
        ]
        note = str(action_result.get("editor_notes") or "").strip()
        if note:
            lines.append(f"审阅说明：{note}")
        return _render_feedback_panel(title="Draft Review 已提交", lines=lines)
    return ""


def _render_keeper_lifecycle_controls(*, session_id: str, snapshot: dict[str, Any]) -> str:
    operator_id = _keeper_operator_id(snapshot)
    target_forms = "".join(
        f"""
        <form method="post" action="/app/sessions/{escape(session_id)}/keeper/lifecycle">
          <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
          <input type="hidden" name="target_status" value="{escape(target_status)}" />
          <button class="button-button {'ghost' if target_status != SessionStatus.COMPLETED.value else 'danger'}" type="submit">{escape(label)}</button>
        </form>
        """
        for target_status, label in _allowed_lifecycle_targets(snapshot.get("status"))
    )
    return (
        f"""
        <article class="list-card">
          <div class="list-head">
            <h3>Lifecycle</h3>
            {_status_pill(snapshot.get('status'))}
          </div>
          <p class="meta-line">operator_id：<code>{escape(operator_id)}</code></p>
          <div class="inline-form-grid">
            {target_forms or '<p class="empty">当前状态没有可继续的生命周期切换。</p>'}
          </div>
        </article>
        """
    )


def _render_keeper_combat_controls(
    *,
    session_id: str,
    snapshot: dict[str, Any],
    participant_by_id: dict[str, dict[str, Any]],
) -> str:
    combat_context = snapshot.get("combat_context") or {}
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    operator_id = _keeper_operator_id(snapshot)
    current_status = str(snapshot.get("status") or SessionStatus.PLANNED.value)
    if current_status != SessionStatus.ACTIVE.value:
        return """
          <article class="list-card">
            <h3>Combat Control</h3>
            <p>只有进行中的会话才能开始或推进战斗顺序。先在上方 lifecycle 区把会话切到 active。</p>
          </article>
        """
    if not combat_context:
        options = "".join(
            f'<option value="{escape(str(participant.get("actor_id") or ""))}">{escape(str(participant.get("display_name") or participant.get("actor_id") or "参与者"))}</option>'
            for participant in participants
            if participant.get("actor_id")
        )
        return f"""
          <article class="list-card">
            <h3>Combat Control</h3>
            <p>战斗未建立时，直接在 keeper workspace 内决定默认起始行动者。</p>
            <form method="post" action="/app/sessions/{escape(session_id)}/keeper/combat/start">
              <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
              <label>
                起始行动者（可选）
                <select name="starting_actor_id">
                  <option value="">按默认顺序开始</option>
                  {options}
                </select>
              </label>
              <button class="button-button secondary" type="submit">开始战斗顺序</button>
            </form>
          </article>
        """
    current_actor_id = str(combat_context.get("current_actor_id") or "")
    current_actor_name = str(
        (participant_by_id.get(current_actor_id) or {}).get("display_name") or current_actor_id or "—"
    )
    return f"""
      <article class="list-card">
        <div class="list-head">
          <h3>Combat Control</h3>
          <span class="tag warn">round {escape(str(combat_context.get('round_number') or '—'))}</span>
        </div>
        <p>当前行动者：{escape(current_actor_name)}</p>
        <form method="post" action="/app/sessions/{escape(session_id)}/keeper/combat/advance">
          <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
          <button class="button-button secondary" type="submit">推进到下一位行动者</button>
        </form>
      </article>
    """


def _render_keeper_workspace_page(
    *,
    session_id: str,
    snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    runtime_assistance: dict[str, Any],
    san_aftermath_suggestions: dict[str, list[dict[str, Any]]],
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
    context_pack: dict[str, Any] | None = None,
    narrative_note_value: str = "",
    narrative_completion_notice: str | None = None,
    narrative_result: LocalLLMAssistantResult | None = None,
    narrative_scope: dict[str, str] | None = None,
    selected_narrative_task: str | None = None,
    assistant_result: LocalLLMAssistantResult | None = None,
    assistant_scope: dict[str, str] | None = None,
    selected_assistant_task: str | None = None,
) -> HTMLResponse:
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    participant_by_id = _participant_map(participants)
    character_states = {
        str(actor_id): state
        for actor_id, state in (snapshot.get("character_states") or {}).items()
        if isinstance(state, dict)
    }
    workflow = keeper_view.get("keeper_workflow") or {}
    summary = workflow.get("summary") or {}
    progress_state = keeper_view.get("progress_state") or {}
    combat_context = snapshot.get("combat_context") or {}
    operator_id = _keeper_operator_id(snapshot)
    assistant_adoption = _keeper_assistant_adoption(
        assistant_result,
        assistant_scope=assistant_scope,
    )
    prompt_completion_notices, draft_completion_notices = _build_keeper_completion_notices(
        action_result,
    )
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    turn_order = combat_context.get("turn_order") or []
    next_actor = None
    if turn_order and combat_context.get("current_turn_index") is not None:
        current_index = int(combat_context.get("current_turn_index") or 0)
        next_actor_entry = turn_order[(current_index + 1) % len(turn_order)]
        next_actor = (participant_by_id.get(str(next_actor_entry.get("actor_id") or "")) or {}).get("display_name") or next_actor_entry.get("actor_id")
    suggestion_count = sum(len(items) for items in san_aftermath_suggestions.values())
    warning_cards = (
        "".join(
            f"""
            <article class="list-card">
              <div class="list-head">
                <h3>{escape(str(item.get('code') or 'warning'))}</h3>
                <span class="tag warn">{escape(str(item.get('scope') or 'warning'))}</span>
              </div>
              <p>{escape(str(item.get('message') or ''))}</p>
            </article>
            """
            for item in warnings[:4]
        )
        if warnings
        else '<p class="empty">当前没有额外导入或外部引用告警。</p>'
    )
    body = (
        _page_head(
            eyebrow="Keeper Workspace",
            title=f"{str(snapshot.get('keeper_name') or 'KP')} 的主持台",
            summary="Keeper 工作区优先保留常驻局势、待处理项、生命周期、战斗推进、伤势 follow-up 与知识入口。只把高频主操作收进 app shell。",
            actions=[
                ("Session Overview", f"/app/sessions/{session_id}", "ghost"),
                ("Knowledge", f"/app/knowledge?{urlencode({'session_id': session_id})}", "secondary"),
                ("Legacy Keeper Detail", f"/playtest/sessions/{session_id}/keeper", ""),
            ],
        )
        + _notice_block(notice)
        + _detail_block(detail)
        + _render_keeper_operation_result(action_result)
        + f"""
        <section class="surface">
          <div class="metric-grid">
            <article class="metric">
              <p class="metric-label">会话状态</p>
              <strong>{escape(_status_label(snapshot.get('status')))}</strong>
              <span>{escape(str(snapshot.get('status') or ''))}</span>
            </article>
            <article class="metric">
              <p class="metric-label">当前场景</p>
              <strong>{escape(current_scene)}</strong>
              <span>{escape(str(beat_id or '无'))} / {escape(str(beat_title or '未命名节点'))}</span>
            </article>
            <article class="metric">
              <p class="metric-label">Active Prompts</p>
              <strong>{escape(str(summary.get('active_prompt_count') or len(workflow.get('active_prompts') or [])))}</strong>
              <span>待 keeper 处理</span>
            </article>
            <article class="metric">
              <p class="metric-label">Objectives</p>
              <strong>{escape(str(summary.get('unresolved_objective_count') or len(workflow.get('unresolved_objectives') or [])))}</strong>
              <span>未完成目标</span>
            </article>
            <article class="metric">
              <p class="metric-label">Drafts</p>
              <strong>{escape(str(len(keeper_view.get('visible_draft_actions') or [])))}</strong>
              <span>待审草稿</span>
            </article>
            <article class="metric">
              <p class="metric-label">San Follow-up</p>
              <strong>{escape(str(suggestion_count))}</strong>
              <span>人工裁定建议</span>
            </article>
          </div>
        </section>
        <section class="surface">
          <div class="surface-header">
            <div>
              <h2>主操作区</h2>
              <p>把 lifecycle、combat start/advance、wound follow-up 直接收进 keeper workspace，不再只给散链接。</p>
            </div>
          </div>
          <div class="surface-grid">
            {_render_keeper_lifecycle_controls(session_id=session_id, snapshot=snapshot)}
            {_render_keeper_combat_controls(session_id=session_id, snapshot=snapshot, participant_by_id=participant_by_id)}
            <article class="list-card">
              <div class="list-head">
                <h3>Prompt / Rules / Knowledge</h3>
                <span class="tag">{escape(str(len(workflow.get('active_prompts') or [])))}</span>
              </div>
              <p>待处理 prompt、规则查询和知识资料继续挂在同一工作区，复杂内容保留旧页承接。</p>
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#prompt-targets">处理 prompts</a>
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/rules">规则查询</a>
                <a class="button-link secondary" href="/app/knowledge?{escape(urlencode({'session_id': session_id}), quote=True)}">打开 Knowledge</a>
              </div>
            </article>
          </div>
        </section>
        <section class="content-grid">
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>待处理事项</h2>
                  <p>先把 prompt / draft / objective 的优先级常驻，不再埋在大段页面里。</p>
                </div>
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#attention">旧版 attention</a>
              </div>
              <div class="surface-grid">
                <article class="list-card">
                  <div class="list-head">
                    <h3>KP Prompts</h3>
                    <span class="tag">{escape(str(len(workflow.get('active_prompts') or [])))}</span>
                  </div>
                  <div class="card-list">
                    {_render_prompt_cards(
                        list(workflow.get('active_prompts') or []),
                        snapshot=snapshot,
                        session_id=session_id,
                        operator_id=operator_id,
                        completion_notices=prompt_completion_notices,
                        assistant_scope=assistant_scope,
                        assistant_adoption=assistant_adoption,
                    )}
                  </div>
                </article>
                <article class="list-card">
                  <div class="list-head">
                    <h3>Draft Review</h3>
                    <span class="tag warn">{escape(str(len(keeper_view.get('visible_draft_actions') or [])))}</span>
                  </div>
                  <div class="card-list">
                    {_render_draft_cards(
                        list(keeper_view.get('visible_draft_actions') or []),
                        snapshot=snapshot,
                        session_id=session_id,
                        reviewer_id=operator_id,
                        completion_notices=draft_completion_notices,
                        assistant_scope=assistant_scope,
                        assistant_adoption=assistant_adoption,
                    )}
                  </div>
                </article>
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>控场摘要</h2>
                  <p>目标推进、beat 位置、当前 scene context 都放在同一块查看。</p>
                </div>
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#live-control">旧版控场页段</a>
              </div>
              <div class="surface-grid">
                <article class="list-card">
                  <h3>当前 scene / beat</h3>
                  <ul class="meta-list">
                    <li>场景：{escape(current_scene)}</li>
                    <li>beat：<span class="mono">{escape(str(beat_id or '无'))}</span></li>
                    <li>beat 标题：{escape(str(beat_title or '未命名节点'))}</li>
                    <li>完成目标历史：{escape(str(len(progress_state.get('completed_objective_history') or [])))}</li>
                    <li>最近 beat 变更：{escape(str(len(progress_state.get('transition_history') or [])))}</li>
                  </ul>
                </article>
                <article class="list-card">
                  <h3>Workflow Summary</h3>
                  <div class="card-list">
                    {
                        ''.join(
                            f'<p>{escape(str(line))}</p>'
                            for line in (summary.get('summary_lines') or [])[:5]
                        )
                        or '<p class="empty">当前没有额外 workflow 摘要。</p>'
                    }
                  </div>
                </article>
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>战斗与伤势</h2>
                  <p>combat / wound / first aid 不是独立孤岛，而是 keeper 工作区的常驻操作层。</p>
                </div>
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#combat-flow">旧版战斗骨架</a>
              </div>
              <div class="surface-grid">
                <article class="list-card">
                  <h3>Combat Context</h3>
                  <ul class="meta-list">
                    <li>当前行动者：{escape(str(combat_context.get('current_actor_id') or '未建立'))}</li>
                    <li>下一位：{escape(str(next_actor or '无'))}</li>
                    <li>回合：{escape(str(combat_context.get('round_number') or '—'))}</li>
                    <li>参与顺序数：{escape(str(len(turn_order)))}</li>
                  </ul>
                  <div class="toolbar">
                    <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#combat-flow">去旧版开始 / 推进战斗</a>
                  </div>
                </article>
                <article class="list-card">
                  <h3>Wound Follow-up</h3>
                  <div class="card-list">
                    {_render_wound_cards(character_states=character_states, participant_by_id=participant_by_id, session_id=session_id, operator_id=_keeper_operator_id(snapshot))}
                  </div>
                </article>
              </div>
            </section>
          </div>
          <div class="card-list">
            {
                _render_keeper_context_pack_block(context_pack=context_pack)
                if context_pack is not None
                else ""
            }
            {_render_keeper_narrative_scaffolding(
                session_id=session_id,
                snapshot=snapshot,
                narrative_note_value=narrative_note_value,
                narrative_completion_notice=narrative_completion_notice,
                narrative_result=narrative_result,
                narrative_scope=narrative_scope,
                selected_narrative_task=selected_narrative_task,
            )}
            {_render_local_llm_assistant_panel(
                title="Keeper Assistant",
                description="可选的本地 LLM 辅助块，只基于当前 keeper 工作区摘要生成非权威建议或草稿。",
                action=f"/app/sessions/{session_id}/keeper/assistant",
                tasks=KEEPER_ASSISTANT_TASKS,
                selected_task=selected_assistant_task,
                result=assistant_result,
                hidden_fields={"narrative_note": narrative_note_value},
                extra_output_html=_render_assistant_draft_source(
                    assistant_scope=assistant_scope,
                    assistant_adoption=assistant_adoption,
                ),
            )}
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>规则与知识辅助</h2>
                  <p>保留 keeper runtime assistance，但把它放进右侧常驻辅助栏。</p>
                </div>
              </div>
              {_render_runtime_assistance(runtime_assistance, session_id=session_id)}
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>San Aftermath</h2>
                  <p>仍由 KP 手动裁定；这里只聚合需要关注的建议数量。</p>
                </div>
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}/keeper#san-aftermath">旧版处理区</a>
              </div>
              <div class="card-list">
                {
                    ''.join(
                        f'''
                        <article class="list-card">
                          <div class="list-head">
                            <h3>{escape(prompt_id)}</h3>
                            <span class="tag warn">{escape(str(len(items)))} 条建议</span>
                          </div>
                          <p>{escape(_excerpt(' / '.join(str(item.get('label') or '') for item in items), limit=120))}</p>
                        </article>
                        '''
                        for prompt_id, items in list(san_aftermath_suggestions.items())[:4]
                    )
                    or '<p class="empty">当前没有 SAN 后续处理建议。</p>'
                }
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>会话工具</h2>
                  <p>checkpoint / export / warning 仍走现有服务层和旧页面。</p>
                </div>
              </div>
              <div class="metric-grid">
                <article class="metric">
                  <p class="metric-label">Checkpoints</p>
                  <strong>{escape(str(len(checkpoints)))}</strong>
                  <span>当前分支点</span>
                </article>
                <article class="metric">
                  <p class="metric-label">Warnings</p>
                  <strong>{escape(str(len(warnings)))}</strong>
                  <span>导入或引用告警</span>
                </article>
              </div>
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/sessions/{escape(session_id)}">Checkpoint 页面</a>
                <a class="button-link ghost" href="/sessions/{escape(session_id)}/snapshot">Snapshot JSON</a>
                <a class="button-link ghost" href="/sessions/{escape(session_id)}/export">Keeper Export JSON</a>
              </div>
              <div class="divider"></div>
              <div class="card-list">{warning_cards}</div>
            </section>
          </div>
        </section>
        """
    )
    return render_web_app_shell(
        title=f"Session {session_id} Keeper Workspace",
        sidebar_html=_render_sidebar(active_section="keeper", session_snapshot=snapshot),
        body_html=body,
    )


def _render_clue_cards(clues: list[dict[str, Any]]) -> str:
    if not clues:
        return '<p class="empty">当前没有对你可见的线索。</p>'
    return "".join(
        f"""
        <article class="list-card">
          <div class="list-head">
            <h3>{escape(str(clue.get('title') or clue.get('clue_id') or '线索'))}</h3>
            <span class="tag">{escape(str(clue.get('status') or 'visible'))}</span>
          </div>
          <p>{escape(_excerpt(clue.get('text'), limit=140))}</p>
        </article>
        """
        for clue in clues[:6]
        if isinstance(clue, dict)
    )


def _render_private_notes(
    private_state: dict[str, Any],
    character_state: dict[str, Any],
) -> str:
    lines = [
        *[str(item) for item in private_state.get("private_notes") or []][:3],
        *[str(item) for item in character_state.get("private_notes") or []][:2],
    ]
    if not lines:
        return '<p class="empty">当前没有可见的私有备注。</p>'
    return "".join(f"<p>{escape(line)}</p>" for line in lines)


def _render_investigator_operation_result(action_result: dict[str, Any] | None) -> str:
    if not action_result:
        return ""
    kind = str(action_result.get("kind") or "")
    payload = action_result.get("payload") or {}
    roll = payload.get("roll") or {}
    if kind == "skill_check":
        return _render_feedback_panel(
            title="最近一次技能检定",
            lines=[
                str(payload.get("message") or ""),
                f"技能：{payload.get('skill_name') or '—'} / {payload.get('skill_value') or '—'}",
                f"掷骰：{roll.get('total') or '—'} / {_roll_outcome_label(roll.get('outcome'))}",
                f"成功：{_bool_label(payload.get('success'))}",
            ],
        )
    if kind == "attribute_check":
        return _render_feedback_panel(
            title="最近一次属性检定",
            lines=[
                str(payload.get("message") or ""),
                f"属性：{payload.get('attribute_name') or '—'} / {payload.get('attribute_value') or '—'}",
                f"掷骰：{roll.get('total') or '—'} / {_roll_outcome_label(roll.get('outcome'))}",
                f"成功：{_bool_label(payload.get('success'))}",
            ],
        )
    if kind == "san_check":
        return _render_feedback_panel(
            title="最近一次理智检定",
            lines=[
                str(payload.get("message") or ""),
                f"来源：{payload.get('source_label') or '—'}",
                f"掷骰：{roll.get('total') or '—'} / {_roll_outcome_label(roll.get('outcome'))}",
                f"SAN：{payload.get('sanity_before') or '—'} -> {payload.get('sanity_after') or '—'}",
                f"损失：{payload.get('loss_applied') or '—'}",
            ],
        )
    if kind == "melee_attack":
        return _render_feedback_panel(
            title="最近一次近战攻击",
            lines=[
                str(payload.get("message") or ""),
                f"目标：{payload.get('target_actor_name') or '—'}",
                f"攻击：{payload.get('attack_label') or '—'} / {payload.get('attack_target_value') or '—'}",
                f"防御：{_defense_mode_label(payload.get('defense_mode'))} / {payload.get('defense_label') or '—'}",
                f"结果：{_attack_resolution_label(payload.get('attack_resolution'))}",
            ],
        )
    if kind == "ranged_attack":
        return _render_feedback_panel(
            title="最近一次远程攻击",
            lines=[
                str(payload.get("message") or ""),
                f"目标：{payload.get('target_actor_name') or '—'}",
                f"攻击：{payload.get('attack_label') or '—'} / {payload.get('attack_target_value') or '—'}",
                f"修正：{payload.get('modifier_label') or '普通攻击'}",
                f"结果：{_attack_resolution_label(payload.get('attack_resolution'))}",
            ],
        )
    if kind == "damage_resolution":
        return _render_feedback_panel(
            title="最近一次伤害结算",
            lines=[
                str(payload.get("message") or ""),
                f"目标：{payload.get('target_actor_name') or '—'}",
                f"伤害：{payload.get('damage_expression') or '—'}",
                f"最终伤害：{payload.get('final_damage') or '—'}",
                f"HP：{payload.get('hp_before') or '—'} -> {payload.get('hp_after') or '—'}",
                f"需 KP follow-up：{_bool_label(payload.get('kp_follow_up_required'))}",
            ],
        )
    if kind == "first_aid":
        return _render_feedback_panel(
            title="最近一次急救",
            lines=[
                str(payload.get("message") or ""),
                f"目标：{payload.get('target_actor_name') or '—'}",
                f"技能：{payload.get('skill_name') or '—'} / {payload.get('skill_value') or '—'}",
                f"掷骰：{roll.get('total') or '—'} / {_roll_outcome_label(roll.get('outcome'))}",
                f"状态：{payload.get('before_state_label') or '—'} -> {payload.get('after_state_label') or '—'}",
            ],
        )
    return ""


def _render_investigator_skill_form(
    *,
    session_id: str,
    viewer_id: str,
    skill_options: list[tuple[str, int]],
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的技能检定。</p>'
    if not skill_options:
        return '<p class="empty">当前角色没有可用的技能检定选项。</p>'
    options = "".join(
        f'<option value="{escape(skill_name)}">{escape(skill_name)} ({escape(str(skill_value))})</option>'
        for skill_name, skill_value in skill_options
    )
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/skill-check">
        <label>
          技能
          <select name="skill_name">{options}</select>
        </label>
        <div class="field-grid">
          <label>
            奖惩骰
            <select name="dice_modifier">
              <option value="normal">普通检定</option>
              <option value="bonus_1">奖励骰 x1</option>
              <option value="bonus_2">奖励骰 x2</option>
              <option value="penalty_1">惩罚骰 x1</option>
              <option value="penalty_2">惩罚骰 x2</option>
            </select>
          </label>
          <label class="checkbox-line">
            <input type="checkbox" name="pushed" value="true" />
            pushed
          </label>
        </div>
        <button class="button-button secondary" type="submit">技能检定</button>
      </form>
    """


def _render_investigator_attribute_form(
    *,
    session_id: str,
    viewer_id: str,
    attribute_options: list[tuple[str, str, int]],
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的属性检定。</p>'
    if not attribute_options:
        return '<p class="empty">当前角色没有可用的属性检定选项。</p>'
    options = "".join(
        f'<option value="{escape(attribute_name)}">{escape(label)} ({escape(str(attribute_value))})</option>'
        for attribute_name, label, attribute_value in attribute_options
    )
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/attribute-check">
        <label>
          属性
          <select name="attribute_name">{options}</select>
        </label>
        <div class="field-grid">
          <label>
            奖惩骰
            <select name="dice_modifier">
              <option value="normal">普通检定</option>
              <option value="bonus_1">奖励骰 x1</option>
              <option value="bonus_2">奖励骰 x2</option>
              <option value="penalty_1">惩罚骰 x1</option>
              <option value="penalty_2">惩罚骰 x2</option>
            </select>
          </label>
          <label class="checkbox-line">
            <input type="checkbox" name="pushed" value="true" />
            pushed
          </label>
        </div>
        <button class="button-button secondary" type="submit">属性检定</button>
      </form>
    """


def _render_investigator_san_form(
    *,
    session_id: str,
    viewer_id: str,
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的理智检定。</p>'
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/san-check">
        <label>
          来源标签
          <input type="text" name="source_label" placeholder="例如：哈斯塔的倒影" required />
        </label>
        <div class="field-grid">
          <label>
            成功损失
            <input type="text" name="success_loss" placeholder="例如：0 或 1" required />
          </label>
          <label>
            失败损失
            <input type="text" name="failure_loss" placeholder="例如：1d3 或 1d6" required />
          </label>
        </div>
        <button class="button-button secondary" type="submit">理智检定</button>
      </form>
    """


def _render_investigator_melee_form(
    *,
    session_id: str,
    viewer_id: str,
    target_options: list[tuple[str, str]],
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的近战攻击判定。</p>'
    if not target_options:
        return '<p class="empty">当前没有可用的近战目标。</p>'
    options = "".join(
        f'<option value="{escape(actor_id)}">{escape(display_name)}</option>'
        for actor_id, display_name in target_options
    )
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/melee-attack">
        <label>
          目标
          <select name="melee_target_actor_id">{options}</select>
        </label>
        <div class="field-grid">
          <label>
            攻击标签
            <input type="text" name="attack_label" placeholder="例如：斗殴" required />
          </label>
          <label>
            攻击值
            <input type="number" name="attack_target_value" min="1" max="100" required />
          </label>
        </div>
        <div class="field-grid">
          <label>
            防御模式
            <select name="defense_mode">
              <option value="dodge">闪避</option>
              <option value="counterattack">反击</option>
            </select>
          </label>
          <label>
            防御标签
            <input type="text" name="defense_label" placeholder="例如：闪避" required />
          </label>
          <label>
            防御值
            <input type="number" name="defense_target_value" min="1" max="100" required />
          </label>
        </div>
        <button class="button-button secondary" type="submit">近战攻击</button>
      </form>
    """


def _render_investigator_ranged_form(
    *,
    session_id: str,
    viewer_id: str,
    target_options: list[tuple[str, str]],
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的远程攻击判定。</p>'
    if not target_options:
        return '<p class="empty">当前没有可用的远程目标。</p>'
    options = "".join(
        f'<option value="{escape(actor_id)}">{escape(display_name)}</option>'
        for actor_id, display_name in target_options
    )
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/ranged-attack">
        <label>
          目标
          <select name="ranged_target_actor_id">{options}</select>
        </label>
        <div class="field-grid">
          <label>
            攻击标签
            <input type="text" name="ranged_attack_label" placeholder="例如：手枪" required />
          </label>
          <label>
            攻击值
            <input type="number" name="ranged_attack_target_value" min="1" max="100" required />
          </label>
        </div>
        <label>
          攻击修正
          <select name="ranged_attack_modifier">
            <option value="normal">普通攻击</option>
            <option value="aim_bonus_1">瞄准一轮</option>
            <option value="hurried_penalty_1">仓促射击</option>
            <option value="burst_penalty_1">连发压制</option>
          </select>
        </label>
        <button class="button-button secondary" type="submit">远程攻击</button>
      </form>
    """


def _render_investigator_damage_form(
    *,
    session_id: str,
    viewer_id: str,
    pending_damage_context: dict[str, Any] | None,
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的伤害结算。</p>'
    if not isinstance(pending_damage_context, dict):
        return '<p class="empty">需要先完成一次命中的攻击判定，才能继续结算伤害。</p>'
    target_actor_id = str(pending_damage_context.get("target_actor_id") or "")
    target_name = str(pending_damage_context.get("target_display_name") or target_actor_id or "—")
    attack_label = str(pending_damage_context.get("attack_label") or "—")
    attack_mode = str(pending_damage_context.get("attack_mode") or "未知")
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/damage-resolution">
        <input type="hidden" name="damage_target_actor_id" value="{escape(target_actor_id)}" />
        <p class="meta-line">待结算：{escape(target_name)} · {escape(attack_mode)} · {escape(attack_label)}</p>
        <div class="field-grid">
          <label>
            伤害表达式
            <input type="text" name="damage_expression" placeholder="例如：1d6+1 或 1d3+db" required />
          </label>
          <label>
            伤害加值（可选）
            <input type="text" name="damage_bonus_expression" placeholder="例如：db" />
          </label>
          <label>
            护甲
            <input type="number" name="armor_value" min="0" max="99" value="0" />
          </label>
        </div>
        <label class="checkbox-line">
          <input type="checkbox" name="skip_hit_location" value="true" />
          命中部位不适用（KP override）
        </label>
        <button class="button-button secondary" type="submit">伤害结算</button>
      </form>
    """


def _render_investigator_first_aid_form(
    *,
    session_id: str,
    viewer_id: str,
    target_options: list[tuple[str, str]],
    skill_options: list[tuple[str, int]],
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return '<p class="empty">本局已结束，当前页面不再进行新的紧急急救。</p>'
    if not skill_options:
        return '<p class="empty">当前角色没有急救或医学技能可用于紧急急救。</p>'
    target_html = "".join(
        f'<option value="{escape(actor_id)}">{escape(display_name)}</option>'
        for actor_id, display_name in target_options
    )
    skill_html = "".join(
        f'<option value="{escape(skill_name)}">{escape(skill_name)} ({escape(str(skill_value))})</option>'
        for skill_name, skill_value in skill_options
    )
    return f"""
      <form method="post" action="/app/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/first-aid">
        <div class="field-grid">
          <label>
            目标
            <select name="first_aid_target_actor_id">{target_html}</select>
          </label>
          <label>
            技能
            <select name="first_aid_skill_name">{skill_html}</select>
          </label>
        </div>
        <button class="button-button secondary" type="submit">紧急急救</button>
      </form>
    """


def _render_investigator_workspace_page(
    *,
    session_id: str,
    viewer_id: str,
    view: dict[str, Any],
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
) -> HTMLResponse:
    participants = [
        participant
        for participant in view.get("participants") or []
        if isinstance(participant, dict)
    ]
    participant_by_id = _participant_map(participants)
    participant = participant_by_id.get(viewer_id) or {}
    own_character_state = view.get("own_character_state") or {}
    own_private_state = view.get("own_private_state") or {}
    viewer_name = str(participant.get("display_name") or viewer_id)
    visible_clues = [
        clue for clue in (view.get("scenario") or {}).get("clues") or [] if isinstance(clue, dict)
    ]
    current_scene = view.get("current_scene") or {}
    combat_context = view.get("combat_context") or {}
    legacy_href = f"/playtest/sessions/{session_id}/investigator/{viewer_id}"
    sidebar_snapshot = {
        "session_id": session_id,
        "scenario": {"title": (view.get("scenario") or {}).get("title")},
        "participants": participants,
        "keeper_id": None,
        "keeper_name": view.get("keeper_name"),
        "current_scene": current_scene,
        "combat_context": combat_context,
    }
    session_status = str(
        view.get("session_status") or view.get("status") or SessionStatus.PLANNED.value
    )
    skill_options = _investigator_skill_options(participant, own_character_state)
    attribute_options = _investigator_attribute_options(participant)
    target_options = _investigator_target_options(participants)
    first_aid_skill_options = _investigator_first_aid_skill_options(
        participant,
        own_character_state,
    )
    pending_damage_context = (
        own_character_state.get("pending_damage_context")
        if isinstance(own_character_state.get("pending_damage_context"), dict)
        else None
    )
    completed_notice = (
        """
        <section class="feedback-panel">
          <h2>本局已结束</h2>
          <p>当前页面保留结束后的查看状态；你仍可查看自己的可见信息和最近结果。</p>
        </section>
        """
        if session_status == SessionStatus.COMPLETED.value
        else ""
    )
    body = (
        _page_head(
            eyebrow="Investigator Workspace",
            title=f"{viewer_name} 的调查员工作区",
            summary="调查员页面优先常驻角色状态、可见线索、最近事件、检定、攻击、伤害与急救；keeper-only 控场信息继续隔离。",
            actions=[
                ("Session Overview", f"/app/sessions/{session_id}", "ghost"),
                ("Legacy Investigator Detail", legacy_href, ""),
                ("Knowledge", f"/app/knowledge?{urlencode({'session_id': session_id})}", "secondary"),
            ],
        )
        + completed_notice
        + _notice_block(notice)
        + _detail_block(detail)
        + _render_investigator_operation_result(action_result)
        + f"""
        <section class="content-grid">
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>常驻状态</h2>
                  <p>HP / SAN / MP / 伤势状态必须常驻，而不是埋在长表单后面。</p>
                </div>
              </div>
              {_character_snapshot(participant, own_character_state)}
              <ul class="meta-list">
                <li>viewer_id：<code>{escape(viewer_id)}</code></li>
                <li>当前场景：{escape(str(current_scene.get('title') or '未知场景'))}</li>
                <li>职业：{escape(str((participant.get('character') or {}).get('occupation') or '调查员'))}</li>
                <li>可见线索数：{escape(str(len(visible_clues)))}</li>
              </ul>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>可见线索</h2>
                  <p>这里只展示该调查员可见的线索，不带 keeper-only clue 细节。</p>
                </div>
              </div>
              <div class="card-list">{_render_clue_cards(visible_clues)}</div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>最近可见事件</h2>
                  <p>按可见范围过滤后的事件时间线。</p>
                </div>
              </div>
              <div class="card-list">
                {_render_event_cards(list(view.get('visible_events') or []), empty_text='当前没有可见事件。', limit=6)}
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>行动与检定</h2>
                  <p>skill / attribute / sanity 直接收进 investigator workspace，避免再跳旧页做高频操作。</p>
                </div>
              </div>
              <div class="surface-grid">
                <article class="list-card">
                  <h3>技能检定</h3>
                  {_render_investigator_skill_form(session_id=session_id, viewer_id=viewer_id, skill_options=skill_options, session_status=session_status)}
                </article>
                <article class="list-card">
                  <h3>属性检定</h3>
                  {_render_investigator_attribute_form(session_id=session_id, viewer_id=viewer_id, attribute_options=attribute_options, session_status=session_status)}
                </article>
                <article class="list-card">
                  <h3>理智检定</h3>
                  {_render_investigator_san_form(session_id=session_id, viewer_id=viewer_id, session_status=session_status)}
                </article>
              </div>
            </section>
          </div>
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>主要动作</h2>
                  <p>attack / damage / first aid 作为常驻操作区内嵌，旧页退居详细结果和兼容承接。</p>
                </div>
              </div>
              <div class="surface-grid">
                <article class="list-card">
                  <h3>近战攻击</h3>
                  {_render_investigator_melee_form(session_id=session_id, viewer_id=viewer_id, target_options=target_options, session_status=session_status)}
                </article>
                <article class="list-card">
                  <h3>远程攻击</h3>
                  {_render_investigator_ranged_form(session_id=session_id, viewer_id=viewer_id, target_options=target_options, session_status=session_status)}
                </article>
                <article class="list-card">
                  <h3>伤害结算</h3>
                  {_render_investigator_damage_form(session_id=session_id, viewer_id=viewer_id, pending_damage_context=pending_damage_context, session_status=session_status)}
                </article>
                <article class="list-card">
                  <h3>紧急急救</h3>
                  {_render_investigator_first_aid_form(session_id=session_id, viewer_id=viewer_id, target_options=target_options, skill_options=first_aid_skill_options, session_status=session_status)}
                </article>
              </div>
              <div class="toolbar">
                <a class="button-link ghost" href="{escape(legacy_href, quote=True)}">旧版调查员详细页</a>
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>战斗摘要</h2>
                  <p>combat 状态挂在调查员工作区右侧，而不是跳到单独页面再找。</p>
                </div>
              </div>
              <ul class="meta-list">
                <li>当前行动者：{escape(str(combat_context.get('current_actor_id') or '未建立战斗顺序'))}</li>
                <li>当前回合：{escape(str(combat_context.get('round_number') or '—'))}</li>
                <li>顺序人数：{escape(str(len(combat_context.get('turn_order') or [])))}</li>
                <li>会话状态：{escape(_status_label(session_status))}</li>
              </ul>
              {
                  f'<p class="meta-line">待结算伤害：{escape(str((own_character_state.get("pending_damage_context") or {}).get("attack_label") or "无"))}</p>'
                  if own_character_state.get("pending_damage_context")
                  else '<p class="meta-line">当前没有待结算伤害上下文。</p>'
              }
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>私有备注</h2>
                  <p>只展示属于该调查员自己的私有记录。</p>
                </div>
              </div>
              <div class="card-list">
                {_render_private_notes(own_private_state, own_character_state)}
              </div>
            </section>
          </div>
        </section>
        """
    )
    return render_web_app_shell(
        title=f"Session {session_id} Investigator {viewer_id}",
        sidebar_html=_render_sidebar(
            active_section="investigator",
            session_snapshot=sidebar_snapshot,
            active_viewer_id=viewer_id,
        ),
        body_html=body,
    )


def _render_knowledge_source_card(source: dict[str, Any], *, session_id: str | None = None) -> str:
    source_id = str(source.get("source_id") or "")
    detail_href = (
        f"/app/knowledge/{quote(source_id)}?{urlencode({'session_id': session_id})}"
        if session_id
        else f"/app/knowledge/{quote(source_id)}"
    )
    summary = _excerpt(
        source.get("normalized_text")
        or source.get("raw_text")
        or (
            f"人物卡提取：{(source.get('character_sheet_extraction') or {}).get('investigator_name')}"
            if source.get("character_sheet_extraction")
            else ""
        ),
        limit=140,
    )
    return f"""
      <article class="list-card">
        <div class="list-head">
          <h3>{escape(str(source.get('source_title_zh') or source_id or '未命名资料'))}</h3>
          <span class="tag">{escape(str(source.get('ingest_status') or 'registered'))}</span>
        </div>
        <p>{escape(summary or '当前资料尚无摘要，可进入详情页查看预览或继续补文本。')}</p>
        <ul class="meta-list">
          <li>source_id：<code>{escape(source_id)}</code></li>
          <li>类型：{escape(str(source.get('source_kind') or 'unknown'))}</li>
          <li>格式：{escape(str(source.get('source_format') or 'unknown'))}</li>
          <li>chunk_count：{escape(str(source.get('chunk_count', 0)))}</li>
        </ul>
        <div class="toolbar">
          <a class="button-link secondary" href="{escape(detail_href, quote=True)}">App 详情</a>
          <a class="button-link ghost" href="/playtest/knowledge/{escape(source_id)}">Legacy 详情</a>
        </div>
      </article>
    """


def _render_knowledge_index_page(
    *,
    sources: list[dict[str, Any]],
    session_id: str | None = None,
) -> HTMLResponse:
    ingested_count = sum(1 for source in sources if source.get("ingest_status") == "ingested")
    character_sheet_count = sum(1 for source in sources if source.get("source_kind") == "character_sheet")
    body = (
        _page_head(
            eyebrow="Knowledge Workspace",
            title="准备资料 / 模板卡 / 扫描",
            summary="knowledge / template-card / scenario scan 更适合作为独立 workspace，再从 create flow 和 keeper 工作区回跳过来，而不是硬塞进 session 详细页。",
            actions=[
                ("登记或扫描资料", "/playtest/knowledge", ""),
                ((f"返回当前 Session" if session_id else "返回 Sessions"), (f"/app/sessions/{session_id}" if session_id else "/app/sessions"), "ghost"),
                ("旧版 Knowledge", "/playtest/knowledge", "ghost"),
            ],
        )
        + f"""
        <section class="surface">
          <div class="metric-grid">
            <article class="metric">
              <p class="metric-label">Sources</p>
              <strong>{escape(str(len(sources)))}</strong>
              <span>已登记资料</span>
            </article>
            <article class="metric">
              <p class="metric-label">Ingested</p>
              <strong>{escape(str(ingested_count))}</strong>
              <span>已入库资料</span>
            </article>
            <article class="metric">
              <p class="metric-label">Template Cards</p>
              <strong>{escape(str(character_sheet_count))}</strong>
              <span>人物卡来源</span>
            </article>
          </div>
        </section>
        <section class="surface">
          <div class="surface-header">
            <div>
              <h2>资料列表</h2>
              <p>这轮先前端化浏览与入口，登记 / ingest / 扫描表单继续走 legacy knowledge 页面。</p>
            </div>
          </div>
          <div class="card-list">
            {
                ''.join(_render_knowledge_source_card(source, session_id=session_id) for source in sources)
                if sources
                else '<p class="empty">当前还没有已登记的知识资料。</p>'
            }
          </div>
        </section>
        """
    )
    sidebar_snapshot = {"session_id": session_id} if session_id else None
    return render_web_app_shell(
        title="Knowledge Workspace",
        sidebar_html=_render_sidebar(active_section="knowledge", session_snapshot=sidebar_snapshot),
        body_html=body,
    )


def _render_knowledge_detail_page(
    *,
    source_id: str,
    source: dict[str, Any],
    preview_chunks: list[dict[str, Any]],
    session_id: str | None = None,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    working_note_value: str = "",
    working_note_completion_notice: str | None = None,
    assistant_result: LocalLLMAssistantResult | None = None,
    assistant_scope: dict[str, str] | None = None,
    selected_assistant_task: str | None = None,
) -> HTMLResponse:
    summary = _excerpt(
        source.get("normalized_text")
        or source.get("raw_text")
        or (
            f"人物卡提取：{(source.get('character_sheet_extraction') or {}).get('investigator_name')}"
            if source.get("character_sheet_extraction")
            else ""
        ),
        limit=220,
    )
    chunk_cards = "".join(
        f"""
        <article class="list-card">
          <div class="list-head">
            <h3>{escape(str(chunk.get('title_zh') or chunk.get('resolved_topic') or chunk.get('topic_key') or '资料片段'))}</h3>
          </div>
          <p>{escape(_excerpt(chunk.get('content') or chunk.get('text'), limit=180))}</p>
        </article>
        """
        for chunk in preview_chunks
    )
    extraction = source.get("character_sheet_extraction") or {}
    working_note_target_id = f"knowledge-work-note-{source_id}"
    working_note_status_id = f"knowledge-work-note-status-{source_id}"
    working_note_flow_status_id = f"knowledge-work-note-flow-status-{source_id}"
    assistant_scope = assistant_scope or _knowledge_source_scope_metadata(source)
    assistant_adoption = _knowledge_assistant_adoption(
        assistant_result,
        source=source,
        assistant_scope=assistant_scope,
    )
    adoption_matches_working_note = _assistant_targets_current_object(
        assistant_adoption,
        target_kind="knowledge_work_note",
        source_object_kind="knowledge_source",
        source_object_id=source_id,
    )
    working_note_adopted_status_text = ""
    if assistant_adoption is not None and adoption_matches_working_note:
        working_note_adopted_status_text = (
            f"已带入 {assistant_adoption['draft_kind_label']}。来源："
            f"{assistant_adoption['source_context_label']} 当前仍需人工编辑并提交。"
        )
    body = (
        _page_head(
            eyebrow="Knowledge Detail",
            title=str(source.get("source_title_zh") or source_id),
            summary="knowledge detail 先进入新的浏览壳；补文本、导入人物卡等修改动作暂时仍由 legacy knowledge 页面承接。",
            actions=[
                (("返回 Knowledge"), (f"/app/knowledge?{urlencode({'session_id': session_id})}" if session_id else "/app/knowledge"), "ghost"),
                ("Legacy Detail", f"/playtest/knowledge/{source_id}", ""),
                ("继续去创建 Session", "/playtest/sessions/create", "secondary"),
            ],
        )
        + _notice_block(notice)
        + _detail_block(detail)
        + f"""
        <section class="content-grid">
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>资料摘要</h2>
                  <p>把 metadata、摘要和预览统一放进一页。</p>
                </div>
              </div>
              <p>{escape(summary or '当前资料还没有可显示的摘要。')}</p>
              <ul class="meta-list">
                <li>source_id：<code>{escape(source_id)}</code></li>
                <li>类型：{escape(str(source.get('source_kind') or 'unknown'))}</li>
                <li>格式：{escape(str(source.get('source_format') or 'unknown'))}</li>
                <li>状态：{escape(str(source.get('ingest_status') or 'registered'))}</li>
                <li>chunk_count：{escape(str(source.get('chunk_count', 0)))}</li>
              </ul>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>内容预览</h2>
                  <p>如果是模板卡提取，这里优先展示关键识别结果。</p>
                </div>
              </div>
              <div class="card-list">
                {
                    chunk_cards
                    or (
                        f'''
                        <article class="list-card">
                          <div class="list-head">
                            <h3>人物卡提取</h3>
                            <span class="tag">{escape(str(extraction.get("template_profile") or "template"))}</span>
                          </div>
                          <p>调查员：{escape(str(extraction.get("investigator_name") or "未命名调查员"))}</p>
                          <p class="meta-line">职业：{escape(str(extraction.get("occupation") or "未标注职业"))}</p>
                          <p class="meta-line">技能数：{escape(str(len(extraction.get("skills") or {})))}</p>
                        </article>
                        '''
                        if extraction
                        else '<p class="empty">当前资料还没有可展示的内容预览。</p>'
                    )
                }
              </div>
            </section>
          </div>
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>下一步</h2>
                  <p>修改动作先保留在旧页面，避免为了前端起盘去重写知识后端。</p>
                </div>
              </div>
              {_render_local_llm_assistant_panel(
                  title="Knowledge Assistant",
                  description="可选的本地 LLM 辅助块，只基于当前资料摘要和预览生成非权威摘要或追问建议。",
                  action=f"/app/knowledge/{quote(source_id)}/assistant",
                  tasks=KNOWLEDGE_ASSISTANT_TASKS,
                  selected_task=selected_assistant_task,
                  result=assistant_result,
                  hidden_fields={
                      "session_id": session_id or "",
                      "working_note": working_note_value,
                  },
                  extra_output_html=_render_assistant_draft_source(
                      assistant_scope=assistant_scope,
                      assistant_adoption=assistant_adoption,
                  ),
              )}
              <div class="divider"></div>
              <section class="surface">
                <div class="surface-header">
                  <div>
                    <h2>当前页工作备注</h2>
                    <p>只用于当前知识页的人手整理，不会写回资料内容或 metadata。</p>
                  </div>
                </div>
                <form method="post" action="/app/knowledge/{quote(source_id)}/working-note" class="form-stack">
                  <input type="hidden" name="session_id" value="{escape(session_id or "", quote=True)}" />
                  <label>
                    工作备注 / 假说
                    <textarea id="{escape(working_note_target_id)}" name="working_note" rows="8" placeholder="可把 assistant 摘要、关键点或追问先整理在这里，再人工确认。">{escape(working_note_value)}</textarea>
                  </label>
                  {_render_assistant_adopt_button(
                      assistant_adoption=assistant_adoption,
                      target_kind="knowledge_work_note",
                      target_id=working_note_target_id,
                      status_id=working_note_status_id,
                      status_text=working_note_adopted_status_text or None,
                      flow_status_id=working_note_flow_status_id,
                      flow_status_text="该草稿来自当前资料页的 assistant 生成。已带入：当前页工作备注框。当前仍待人工编辑并提交。",
                      source_object_kind="knowledge_source",
                      source_object_id=source_id,
                  )}
                  {
                      (
                          f'<p id="{escape(working_note_status_id)}" class="helper adoption-status">当前可采纳：'
                          f"{escape(assistant_adoption['draft_kind_label'])}。来源："
                          f"{escape(assistant_adoption['source_context_label'])} 目标：当前页工作备注框。"
                          ' 只会带入文本，不会自动提交。</p>'
                      )
                      if adoption_matches_working_note
                      else ""
                  }
                  {
                      (
                          f'<p id="{escape(working_note_flow_status_id)}" class="helper assistant-flow-status">'
                          '当前尚未带入。若采纳，将带入当前页工作备注框，之后仍需人工编辑并提交。</p>'
                      )
                      if adoption_matches_working_note
                      else ""
                  }
                  <button class="button-button secondary" type="submit">确认当前页工作备注</button>
                </form>
                {
                    (
                        f'<p class="helper assistant-completion-status">{escape(working_note_completion_notice)}</p>'
                    )
                    if working_note_completion_notice
                    else ""
                }
              </section>
              <div class="divider"></div>
              <div class="toolbar">
                <a class="button-link ghost" href="/playtest/knowledge/{escape(source_id)}">补文本 / ingest</a>
                <a class="button-link ghost" href="/playtest/knowledge">登记更多资料</a>
                <a class="button-link ghost" href="/playtest/sessions/create">去创建 Session</a>
              </div>
            </section>
          </div>
        </section>
        """
    )
    sidebar_snapshot = {"session_id": session_id} if session_id else None
    return render_web_app_shell(
        title=f"Knowledge {source_id}",
        sidebar_html=_render_sidebar(active_section="knowledge", session_snapshot=sidebar_snapshot),
        body_html=body,
    )

def _render_recap_page(
    *,
    session_id: str,
    snapshot: dict[str, Any],
    context_pack: dict[str, Any] | None = None,
    assistant_result: LocalLLMAssistantResult | None = None,
    selected_assistant_task: str | None = None,
) -> HTMLResponse:
    current_scene, beat_id, beat_title = _scene_and_beat(snapshot)
    reviewed_count = len(snapshot.get("reviewed_actions") or [])
    authoritative_count = len(snapshot.get("authoritative_actions") or [])
    body = (
        _page_head(
            eyebrow="Recap / Review",
            title=f"{str((snapshot.get('scenario') or {}).get('title') or session_id)} 回顾",
            summary="recap 不再只是 session 尾页，而是 session workspace 的固定一层，用来看时间线、audit 与 review closeout。",
            actions=[
                ("Session Overview", f"/app/sessions/{session_id}", "ghost"),
                ("Keeper Workspace", f"/app/sessions/{session_id}/keeper", "secondary"),
                ("Legacy Recap", f"/playtest/sessions/{session_id}/recap", ""),
            ],
        )
        + f"""
        <section class="surface">
          <div class="metric-grid">
            <article class="metric">
              <p class="metric-label">状态</p>
              <strong>{escape(_status_label(snapshot.get('status')))}</strong>
              <span>{escape(str(snapshot.get('status') or ''))}</span>
            </article>
            <article class="metric">
              <p class="metric-label">当前场景 / Beat</p>
              <strong>{escape(current_scene)}</strong>
              <span>{escape(str(beat_id or '无'))} / {escape(str(beat_title or '未命名节点'))}</span>
            </article>
            <article class="metric">
              <p class="metric-label">Timeline</p>
              <strong>{escape(str(len(snapshot.get('timeline') or [])))}</strong>
              <span>已记录事件</span>
            </article>
            <article class="metric">
              <p class="metric-label">Review</p>
              <strong>{escape(str(reviewed_count))}</strong>
              <span>reviewed actions</span>
            </article>
            <article class="metric">
              <p class="metric-label">Authoritative</p>
              <strong>{escape(str(authoritative_count))}</strong>
              <span>authoritative actions</span>
            </article>
          </div>
        </section>
        <section class="content-grid">
          <div class="card-list">
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>最近时间线</h2>
                  <p>按最近记录优先展示，保持 closeout 可回看。</p>
                </div>
              </div>
              <div class="card-list">
                {_render_event_cards(list(reversed(snapshot.get('timeline') or [])), empty_text='当前还没有时间线事件。', limit=8)}
              </div>
            </section>
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>Audit / Review</h2>
                  <p>keeper review gate、lifecycle、hook material 更新等管理动作都在这里回看。</p>
                </div>
              </div>
              <div class="card-list">
                {_render_audit_cards(list(snapshot.get('audit_log') or []), empty_text='当前还没有管理审计记录。', limit=8)}
              </div>
            </section>
          </div>
          <div class="card-list">
            {
                _render_keeper_context_pack_block(
                    context_pack=context_pack,
                    title="Keeper Context Pack",
                    summary="把 keeper-side 当前局势压缩层固定挂在 recap 页，便于 closeout 回看与后续 AI 输入复用。",
                )
                if context_pack is not None
                else ""
            }
            {_render_local_llm_assistant_panel(
                title="Recap Assistant",
                description="可选的本地 LLM 辅助块，只基于回顾页当前上下文生成 recap 草稿或待办摘要。",
                action=f"/app/sessions/{session_id}/recap/assistant",
                tasks=RECAP_ASSISTANT_TASKS,
                selected_task=selected_assistant_task,
                result=assistant_result,
            )}
            <section class="surface">
              <div class="surface-header">
                <div>
                  <h2>Closeout 摘要</h2>
                  <p>回顾页保留为 summary-first，而不是展开所有 payload。</p>
                </div>
              </div>
              <ul class="meta-list">
                <li>session_id：<code>{escape(session_id)}</code></li>
                <li>KP：{escape(str(snapshot.get('keeper_name') or 'KP'))}</li>
                <li>分组：{escape(_group_label(snapshot.get('playtest_group')))}</li>
                <li>当前场景：{escape(current_scene)}</li>
                <li>当前 beat：<span class="mono">{escape(str(beat_id or '无'))}</span></li>
                <li>reviewed_actions：{escape(str(reviewed_count))}</li>
                <li>authoritative_actions：{escape(str(authoritative_count))}</li>
              </ul>
            </section>
          </div>
        </section>
        """
    )
    return render_web_app_shell(
        title=f"Session {session_id} Recap",
        sidebar_html=_render_sidebar(active_section="recap", session_snapshot=snapshot),
        body_html=body,
    )


def _render_app_keeper_from_service(
    *,
    service: SessionService,
    session_id: str,
    local_llm_service: LocalLLMService | None = None,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
    context_pack: dict[str, Any] | None = None,
    narrative_note_value: str = "",
    narrative_completion_notice: str | None = None,
    narrative_result: LocalLLMAssistantResult | None = None,
    narrative_scope: dict[str, str] | None = None,
    selected_narrative_task: str | None = None,
    assistant_result: LocalLLMAssistantResult | None = None,
    assistant_scope: dict[str, str] | None = None,
    selected_assistant_task: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Keeper Workspace",
            title="Keeper 工作区不可用",
            summary="当前无法加载 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Keeper Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if assistant_result is None and local_llm_service is not None and not local_llm_service.enabled:
        assistant_result = _disabled_assistant_result(
            workspace_key="keeper_workspace",
            tasks=KEEPER_ASSISTANT_TASKS,
            selected_task=selected_assistant_task,
        )
    if narrative_result is None and local_llm_service is not None and not local_llm_service.enabled:
        narrative_result = _disabled_assistant_result(
            workspace_key="keeper_narrative_scaffolding",
            tasks=KEEPER_NARRATIVE_TASKS,
            selected_task=selected_narrative_task,
        )
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    if context_pack is None:
        context_pack = _build_keeper_context_pack_payload(
            service=service,
            session=session,
            keeper_view=keeper_view,
            runtime_assistance=runtime_assistance,
            narrative_note_value=narrative_note_value,
        )
    response = _render_keeper_workspace_page(
        session_id=session_id,
        snapshot=session.model_dump(mode="json"),
        keeper_view=keeper_view.model_dump(mode="json"),
        checkpoints=[checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        warnings=[warning.model_dump(mode="json") for warning in warnings],
        runtime_assistance=runtime_assistance,
        san_aftermath_suggestions=service.get_keeper_san_aftermath_suggestions(session=session),
        notice=notice,
        detail=detail,
        action_result=action_result,
        context_pack=context_pack,
        narrative_note_value=narrative_note_value,
        narrative_completion_notice=narrative_completion_notice,
        narrative_result=narrative_result,
        narrative_scope=narrative_scope,
        selected_narrative_task=selected_narrative_task,
        assistant_result=assistant_result,
        assistant_scope=assistant_scope,
        selected_assistant_task=selected_assistant_task,
    )
    response.status_code = status_code
    return response


def _render_app_knowledge_detail_from_service(
    *,
    knowledge_service: KnowledgeService,
    source_id: str,
    local_llm_service: LocalLLMService | None = None,
    session_id: str | None = None,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    working_note_value: str = "",
    working_note_completion_notice: str | None = None,
    assistant_result: LocalLLMAssistantResult | None = None,
    assistant_scope: dict[str, str] | None = None,
    selected_assistant_task: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        source, preview_chunks = knowledge_service.get_source_preview(source_id, limit=4)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Knowledge Detail",
            title="资料不存在",
            summary="当前无法加载知识资料详情。",
            actions=[("返回 Knowledge", "/app/knowledge", "ghost")],
        )
        sidebar_snapshot = {"session_id": session_id} if session_id else None
        return render_web_app_shell(
            title=f"Knowledge {source_id} Missing",
            sidebar_html=_render_sidebar(active_section="knowledge", session_snapshot=sidebar_snapshot),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if assistant_result is None and local_llm_service is not None and not local_llm_service.enabled:
        assistant_result = _disabled_assistant_result(
            workspace_key="knowledge_detail",
            tasks=KNOWLEDGE_ASSISTANT_TASKS,
            selected_task=selected_assistant_task,
        )
    response = _render_knowledge_detail_page(
        source_id=source_id,
        source=source.model_dump(mode="json"),
        preview_chunks=[chunk.model_dump(mode="json") for chunk in preview_chunks],
        session_id=session_id,
        notice=notice,
        detail=detail,
        working_note_value=working_note_value,
        working_note_completion_notice=working_note_completion_notice,
        assistant_result=assistant_result,
        assistant_scope=assistant_scope,
        selected_assistant_task=selected_assistant_task,
    )
    response.status_code = status_code
    return response


def _render_app_recap_from_service(
    *,
    service: SessionService,
    session_id: str,
    local_llm_service: LocalLLMService | None = None,
    assistant_result: LocalLLMAssistantResult | None = None,
    selected_assistant_task: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Recap / Review",
            title="Recap 不可用",
            summary="当前无法加载 session recap。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Recap Missing",
            sidebar_html=_render_sidebar(active_section="recap"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
    ).model_dump(mode="json")
    if assistant_result is None and local_llm_service is not None and not local_llm_service.enabled:
        assistant_result = _disabled_assistant_result(
            workspace_key="session_recap",
            tasks=RECAP_ASSISTANT_TASKS,
            selected_task=selected_assistant_task,
        )
    response = _render_recap_page(
        session_id=session_id,
        snapshot=snapshot,
        context_pack=context_pack,
        assistant_result=assistant_result,
        selected_assistant_task=selected_assistant_task,
    )
    response.status_code = status_code
    return response


def _render_app_investigator_from_service(
    *,
    service: SessionService,
    session_id: str,
    viewer_id: str,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        view = service.get_session_view(
            session_id,
            viewer_id=viewer_id,
            viewer_role=ViewerRole.INVESTIGATOR,
        ).model_dump(mode="json")
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Investigator Workspace",
            title="调查员工作区不可用",
            summary="当前无法加载 investigator workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Investigator Missing",
            sidebar_html=_render_sidebar(active_section="investigator"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    response = _render_investigator_workspace_page(
        session_id=session_id,
        viewer_id=viewer_id,
        view=view,
        notice=notice,
        detail=detail,
        action_result=action_result,
    )
    response.status_code = status_code
    return response


@router.get("/", include_in_schema=False)
def web_app_root() -> RedirectResponse:
    return RedirectResponse(url="/app/sessions", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/setup", response_class=HTMLResponse)
def web_app_setup(
    playtest_group: str | None = None,
) -> HTMLResponse:
    form_values = _default_playtest_setup_form_values()
    normalized_group = _normalize_form_text(playtest_group)
    if normalized_group:
        form_values["playtest_group"] = normalized_group
    return _render_setup_page(form_values=form_values)


@router.post("/setup", response_class=HTMLResponse)
async def web_app_create_session(
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = _normalize_playtest_setup_form_values(await _read_form_payload(request))
    try:
        start_request = _build_playtest_setup_request(form)
        response = service.start_session(start_request)
        return RedirectResponse(
            url=f"/app/sessions/{response.session_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValidationError, ValueError) as exc:
        return _render_setup_page(
            form_values=form,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.get("/sessions", response_class=HTMLResponse)
def web_app_sessions(
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_sessions_page(
        sessions=[session.model_dump(mode="json") for session in service.list_sessions()]
    )


@router.get("/groups/{group_name}", response_class=HTMLResponse)
def web_app_group(
    group_name: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    sessions = [
        session.model_dump(mode="json")
        for session in service.list_sessions()
        if _group_label(session.playtest_group, empty_label="") == group_name
    ]
    return _render_group_page(group_name=group_name, sessions=sessions)


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def web_app_session_overview(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    try:
        snapshot = service.snapshot_session(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Session Overview",
            title="Session 未找到",
            summary="当前无法建立 session workspace 上下文。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Missing",
            sidebar_html=_render_sidebar(active_section="sessions"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _render_session_overview_page(session_id=session_id, snapshot=snapshot)


@router.get("/sessions/{session_id}/keeper", response_class=HTMLResponse)
def web_app_keeper_workspace(
    session_id: str,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    return _render_app_keeper_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
    )


@router.post("/sessions/{session_id}/keeper/assistant", response_class=HTMLResponse)
async def web_app_keeper_assistant(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    narrative_note_value = _normalize_form_text(form.get("narrative_note")) or ""
    selected_task, task_label = _assistant_task_selection(
        KEEPER_ASSISTANT_TASKS,
        _normalize_form_text(form.get("assistant_task")),
    )
    try:
        session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Keeper Workspace",
            title="Keeper 工作区不可用",
            summary="当前无法加载 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Keeper Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    san_aftermath_suggestions = service.get_keeper_san_aftermath_suggestions(session=session)
    context_pack = _build_keeper_context_pack_payload(
        service=service,
        session=session,
        keeper_view=keeper_view,
        runtime_assistance=runtime_assistance,
        narrative_note_value=narrative_note_value,
    )
    assistant_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="keeper_workspace",
        task_key=selected_task,
        task_label=task_label,
        context=_build_keeper_assistant_context(
            snapshot=session.model_dump(mode="json"),
            keeper_view=keeper_view.model_dump(mode="json"),
            runtime_assistance=runtime_assistance,
            san_aftermath_suggestions=san_aftermath_suggestions,
            context_pack=context_pack,
        ),
    )
    return _render_keeper_workspace_page(
        session_id=session_id,
        snapshot=session.model_dump(mode="json"),
        keeper_view=keeper_view.model_dump(mode="json"),
        checkpoints=[checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        warnings=[warning.model_dump(mode="json") for warning in warnings],
        runtime_assistance=runtime_assistance,
        san_aftermath_suggestions=san_aftermath_suggestions,
        context_pack=context_pack,
        narrative_note_value=narrative_note_value,
        assistant_result=assistant_result,
        selected_assistant_task=selected_task,
    )


@router.post("/sessions/{session_id}/keeper/narrative-assistant", response_class=HTMLResponse)
async def web_app_keeper_narrative_assistant(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    narrative_note_value = _normalize_form_text(form.get("narrative_note")) or ""
    selected_task, task_label = _assistant_task_selection(
        KEEPER_NARRATIVE_TASKS,
        _normalize_form_text(form.get("assistant_task")),
    )
    try:
        session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Keeper Workspace",
            title="Keeper 工作区不可用",
            summary="当前无法加载 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Keeper Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    san_aftermath_suggestions = service.get_keeper_san_aftermath_suggestions(session=session)
    snapshot = session.model_dump(mode="json")
    context_pack = _build_keeper_context_pack_payload(
        service=service,
        session=session,
        keeper_view=keeper_view,
        runtime_assistance=runtime_assistance,
        narrative_note_value=narrative_note_value,
    )
    narrative_scope = _keeper_narrative_scope_metadata(
        session_id=session_id,
        snapshot=snapshot,
    )
    narrative_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="keeper_narrative_scaffolding",
        task_key=selected_task,
        task_label=task_label,
        context=_build_keeper_narrative_context(
            snapshot=snapshot,
            keeper_view=keeper_view.model_dump(mode="json"),
            runtime_assistance=runtime_assistance,
            context_pack=context_pack,
        ),
    )
    return _render_keeper_workspace_page(
        session_id=session_id,
        snapshot=snapshot,
        keeper_view=keeper_view.model_dump(mode="json"),
        checkpoints=[checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        warnings=[warning.model_dump(mode="json") for warning in warnings],
        runtime_assistance=runtime_assistance,
        san_aftermath_suggestions=san_aftermath_suggestions,
        context_pack=context_pack,
        narrative_note_value=narrative_note_value,
        narrative_result=narrative_result,
        narrative_scope=narrative_scope,
        selected_narrative_task=selected_task,
    )


@router.post("/sessions/{session_id}/keeper/narrative-note", response_class=HTMLResponse)
async def web_app_keeper_narrative_note(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    narrative_note_value = _normalize_form_text(form.get("narrative_note")) or ""
    notice = (
        "当前剧情工作备注已人工确认；仅保留在当前返回页，不会写入 session 主状态。"
        if narrative_note_value
        else "当前剧情工作备注已清空；不会写入 session 主状态。"
    )
    completion_notice = (
        "当前剧情工作备注已人工提交，本轮剧情支架建议链已结束。"
        if narrative_note_value
        else "当前剧情工作备注已清空，当前页已恢复默认状态。"
    )
    return _render_app_keeper_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
        notice=notice,
        narrative_note_value=narrative_note_value,
        narrative_completion_notice=completion_notice,
    )


@router.post("/sessions/{session_id}/keeper/prompts/{prompt_id}/assistant", response_class=HTMLResponse)
def web_app_keeper_prompt_assistant(
    session_id: str,
    prompt_id: str,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    try:
        session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Keeper Workspace",
            title="Keeper 工作区不可用",
            summary="当前无法加载 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Keeper Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    keeper_view_snapshot = keeper_view.model_dump(mode="json")
    prompt = next(
        (
            item
            for item in list((keeper_view_snapshot.get("keeper_workflow") or {}).get("active_prompts") or [])
            if str(item.get("prompt_id") or "") == prompt_id
        ),
        None,
    )
    if prompt is None:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=f"当前无法定位 prompt {prompt_id} 的 keeper 可见上下文。",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    san_aftermath_suggestions = service.get_keeper_san_aftermath_suggestions(session=session)
    context_pack = _build_keeper_context_pack_payload(
        service=service,
        session=session,
        keeper_view=keeper_view,
        runtime_assistance=runtime_assistance,
    )
    assistant_context = _build_keeper_prompt_object_assistant_context(
        snapshot=snapshot,
        keeper_view=keeper_view_snapshot,
        prompt=prompt,
    )
    assistant_scope = _keeper_prompt_scope_metadata(
        prompt,
        local_context_summary=_normalize_form_text(
            (assistant_context.get("prompt_local_context") or {}).get("context_summary")
        ),
    )
    assistant_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="keeper_workspace",
        task_key="note_draft",
        task_label=KEEPER_ASSISTANT_TASKS["note_draft"],
        context=assistant_context,
    )
    return _render_keeper_workspace_page(
        session_id=session_id,
        snapshot=snapshot,
        keeper_view=keeper_view_snapshot,
        checkpoints=[checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        warnings=[warning.model_dump(mode="json") for warning in warnings],
        runtime_assistance=runtime_assistance,
        san_aftermath_suggestions=san_aftermath_suggestions,
        context_pack=context_pack,
        assistant_result=assistant_result,
        assistant_scope=assistant_scope,
        selected_assistant_task="note_draft",
    )


@router.post("/sessions/{session_id}/draft-actions/{draft_id}/assistant", response_class=HTMLResponse)
def web_app_keeper_draft_assistant(
    session_id: str,
    draft_id: str,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    try:
        session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Keeper Workspace",
            title="Keeper 工作区不可用",
            summary="当前无法加载 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Keeper Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    keeper_view_snapshot = keeper_view.model_dump(mode="json")
    draft = next(
        (
            item
            for item in list(keeper_view_snapshot.get("visible_draft_actions") or [])
            if str(item.get("draft_id") or "") == draft_id
        ),
        None,
    )
    if draft is None:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=f"当前无法定位待审草稿 {draft_id} 的 keeper 可见上下文。",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    san_aftermath_suggestions = service.get_keeper_san_aftermath_suggestions(session=session)
    context_pack = _build_keeper_context_pack_payload(
        service=service,
        session=session,
        keeper_view=keeper_view,
        runtime_assistance=runtime_assistance,
    )
    assistant_context = _build_keeper_draft_object_assistant_context(
        snapshot=snapshot,
        keeper_view=keeper_view_snapshot,
        draft=draft,
    )
    assistant_scope = _keeper_draft_scope_metadata(
        draft,
        local_context_summary=_normalize_form_text(
            (assistant_context.get("draft_local_context") or {}).get("context_summary")
        ),
    )
    assistant_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="keeper_workspace",
        task_key="draft_review_note_draft",
        task_label=KEEPER_ASSISTANT_TASKS["draft_review_note_draft"],
        context=assistant_context,
    )
    return _render_keeper_workspace_page(
        session_id=session_id,
        snapshot=snapshot,
        keeper_view=keeper_view_snapshot,
        checkpoints=[checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        warnings=[warning.model_dump(mode="json") for warning in warnings],
        runtime_assistance=runtime_assistance,
        san_aftermath_suggestions=san_aftermath_suggestions,
        context_pack=context_pack,
        assistant_result=assistant_result,
        assistant_scope=assistant_scope,
        selected_assistant_task="draft_review_note_draft",
    )


@router.post("/sessions/{session_id}/keeper/prompts/{prompt_id}/status", response_class=HTMLResponse)
async def web_app_keeper_prompt_status(
    session_id: str,
    prompt_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    note = _normalize_form_text(form.get("note")) or None
    try:
        response = service.update_keeper_prompt_status(
            session_id,
            prompt_id,
            UpdateKeeperPromptRequest(
                operator_id=form.get("operator_id", ""),
                status=form.get("status"),
                add_notes=[note] if note else [],
            ),
        )
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            notice=response.message,
            action_result={
                "kind": "prompt_status",
                "target_id": prompt_id,
                "payload": response.model_dump(mode="json"),
                "note": note,
            },
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/draft-actions/{draft_id}/review", response_class=HTMLResponse)
async def web_app_keeper_draft_review(
    session_id: str,
    draft_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    editor_notes = _normalize_form_text(form.get("editor_notes")) or None
    try:
        response = service.review_draft_action(
            session_id,
            draft_id,
            ReviewDraftRequest(
                reviewer_id=form.get("reviewer_id", ""),
                decision=form.get("decision"),
                editor_notes=editor_notes,
            ),
        )
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            notice=response.message,
            action_result={
                "kind": "draft_review",
                "target_id": draft_id,
                "payload": response.model_dump(mode="json"),
                "editor_notes": editor_notes,
            },
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.get("/sessions/{session_id}/investigator/{viewer_id}", response_class=HTMLResponse)
def web_app_investigator_workspace(
    session_id: str,
    viewer_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_app_investigator_from_service(
        service=service,
        session_id=session_id,
        viewer_id=viewer_id,
    )


@router.post("/sessions/{session_id}/keeper/lifecycle", response_class=HTMLResponse)
async def web_app_keeper_lifecycle(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.update_keeper_session_lifecycle(
            session_id,
            UpdateSessionLifecycleRequest(
                operator_id=form.get("operator_id", ""),
                target_status=form.get("target_status", ""),
            ),
        )
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            notice=response.message,
            action_result={"kind": "lifecycle", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/keeper/combat/start", response_class=HTMLResponse)
async def web_app_keeper_combat_start(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.start_combat_context(
            session_id,
            StartCombatContextRequest(
                operator_id=form.get("operator_id", ""),
                starting_actor_id=_normalize_form_text(form.get("starting_actor_id")),
            ),
        )
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            notice=response.message,
            action_result={"kind": "combat_start", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/keeper/combat/advance", response_class=HTMLResponse)
async def web_app_keeper_combat_advance(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.advance_combat_turn(
            session_id,
            AdvanceCombatTurnRequest(operator_id=form.get("operator_id", "")),
        )
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            notice=response.message,
            action_result={"kind": "combat_advance", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/keeper/wounds/{actor_id}/resolve", response_class=HTMLResponse)
async def web_app_keeper_wound_resolution(
    session_id: str,
    actor_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.resolve_keeper_wound_status(
            session_id,
            actor_id,
            KeeperWoundResolutionRequest(
                operator_id=form.get("operator_id", ""),
                resolution=_normalize_form_text(form.get("resolution")) or "",
            ),
        )
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            notice=response.message,
            action_result={"kind": "wound_resolution", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_app_keeper_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/skill-check", response_class=HTMLResponse)
async def web_app_investigator_skill_check(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        bonus_dice, penalty_dice = _parse_investigator_dice_modifier(
            _normalize_form_text(form.get("dice_modifier")) or "normal"
        )
        response = service.perform_investigator_skill_check(
            session_id,
            InvestigatorSkillCheckRequest(
                actor_id=viewer_id,
                skill_name=_normalize_form_text(form.get("skill_name")) or "",
                bonus_dice=bonus_dice,
                penalty_dice=penalty_dice,
                pushed=_normalize_form_text(form.get("pushed")) == "true",
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "skill_check", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/attribute-check", response_class=HTMLResponse)
async def web_app_investigator_attribute_check(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        bonus_dice, penalty_dice = _parse_investigator_dice_modifier(
            _normalize_form_text(form.get("dice_modifier")) or "normal"
        )
        response = service.perform_investigator_attribute_check(
            session_id,
            InvestigatorAttributeCheckRequest(
                actor_id=viewer_id,
                attribute_name=_normalize_form_text(form.get("attribute_name")) or "",
                bonus_dice=bonus_dice,
                penalty_dice=penalty_dice,
                pushed=_normalize_form_text(form.get("pushed")) == "true",
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "attribute_check", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/san-check", response_class=HTMLResponse)
async def web_app_investigator_san_check(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.perform_investigator_san_check(
            session_id,
            InvestigatorSanCheckRequest(
                actor_id=viewer_id,
                source_label=_normalize_form_text(form.get("source_label")) or "",
                success_loss=_normalize_form_text(form.get("success_loss")) or "",
                failure_loss=_normalize_form_text(form.get("failure_loss")) or "",
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "san_check", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/melee-attack", response_class=HTMLResponse)
async def web_app_investigator_melee_attack(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.perform_investigator_melee_attack(
            session_id,
            InvestigatorMeleeAttackRequest(
                actor_id=viewer_id,
                target_actor_id=_normalize_form_text(form.get("melee_target_actor_id")) or "",
                attack_label=_normalize_form_text(form.get("attack_label")) or "",
                attack_target_value=_normalize_form_text(form.get("attack_target_value")) or "",
                defense_mode=_normalize_form_text(form.get("defense_mode")) or "dodge",
                defense_label=_normalize_form_text(form.get("defense_label")) or "",
                defense_target_value=_normalize_form_text(form.get("defense_target_value")) or "",
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "melee_attack", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/ranged-attack", response_class=HTMLResponse)
async def web_app_investigator_ranged_attack(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        bonus_dice, penalty_dice, modifier_label = _parse_investigator_ranged_attack_modifier(
            _normalize_form_text(form.get("ranged_attack_modifier")) or "normal"
        )
        response = service.perform_investigator_ranged_attack(
            session_id,
            InvestigatorRangedAttackRequest(
                actor_id=viewer_id,
                target_actor_id=_normalize_form_text(form.get("ranged_target_actor_id")) or "",
                attack_label=_normalize_form_text(form.get("ranged_attack_label")) or "",
                attack_target_value=_normalize_form_text(form.get("ranged_attack_target_value")) or "",
                bonus_dice=bonus_dice,
                penalty_dice=penalty_dice,
                modifier_label=modifier_label,
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "ranged_attack", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/damage-resolution", response_class=HTMLResponse)
async def web_app_investigator_damage_resolution(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.resolve_investigator_damage(
            session_id,
            InvestigatorDamageResolutionRequest(
                actor_id=viewer_id,
                target_actor_id=_normalize_form_text(form.get("damage_target_actor_id")) or "",
                damage_expression=_normalize_form_text(form.get("damage_expression")) or "",
                damage_bonus_expression=_normalize_form_text(form.get("damage_bonus_expression")),
                armor_value=_normalize_form_text(form.get("armor_value")) or "0",
                skip_hit_location=_normalize_form_text(form.get("skip_hit_location")) == "true",
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "damage_resolution", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/first-aid", response_class=HTMLResponse)
async def web_app_investigator_first_aid(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.perform_investigator_first_aid(
            session_id,
            InvestigatorFirstAidRequest(
                actor_id=viewer_id,
                target_actor_id=_normalize_form_text(form.get("first_aid_target_actor_id")) or "",
                skill_name=_normalize_form_text(form.get("first_aid_skill_name")) or "",
            ),
        )
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result={"kind": "first_aid", "payload": response.model_dump(mode="json")},
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_app_investigator_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            detail=extract_error_detail(exc),
            status_code=_exception_status_code(exc),
        )


@router.get("/knowledge", response_class=HTMLResponse)
def web_app_knowledge_index(
    session_id: str | None = None,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    return _render_knowledge_index_page(
        sources=[source.model_dump(mode="json") for source in knowledge_service.list_sources()],
        session_id=session_id,
    )


@router.get("/knowledge/{source_id}", response_class=HTMLResponse)
def web_app_knowledge_detail(
    source_id: str,
    session_id: str | None = None,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    return _render_app_knowledge_detail_from_service(
        knowledge_service=knowledge_service,
        source_id=source_id,
        local_llm_service=local_llm_service,
        session_id=session_id,
    )


@router.post("/knowledge/{source_id}/assistant", response_class=HTMLResponse)
async def web_app_knowledge_assistant(
    source_id: str,
    request: Request,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    session_id = _normalize_form_text(form.get("session_id"))
    working_note_value = _normalize_form_text(form.get("working_note")) or ""
    selected_task, task_label = _assistant_task_selection(
        KNOWLEDGE_ASSISTANT_TASKS,
        _normalize_form_text(form.get("assistant_task")),
    )
    try:
        source, preview_chunks = knowledge_service.get_source_preview(source_id, limit=4)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Knowledge Detail",
            title="资料不存在",
            summary="当前无法加载知识资料详情。",
            actions=[("返回 Knowledge", "/app/knowledge", "ghost")],
        )
        sidebar_snapshot = {"session_id": session_id} if session_id else None
        return render_web_app_shell(
            title=f"Knowledge {source_id} Missing",
            sidebar_html=_render_sidebar(active_section="knowledge", session_snapshot=sidebar_snapshot),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assistant_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="knowledge_detail",
        task_key=selected_task,
        task_label=task_label,
        context=_build_knowledge_assistant_context(
            source=source.model_dump(mode="json"),
            preview_chunks=[chunk.model_dump(mode="json") for chunk in preview_chunks],
        ),
    )
    return _render_knowledge_detail_page(
        source_id=source_id,
        source=source.model_dump(mode="json"),
        preview_chunks=[chunk.model_dump(mode="json") for chunk in preview_chunks],
        session_id=session_id,
        working_note_value=working_note_value,
        assistant_result=assistant_result,
        assistant_scope=_knowledge_source_scope_metadata(source.model_dump(mode="json")),
        selected_assistant_task=selected_task,
    )


@router.post("/knowledge/{source_id}/working-note", response_class=HTMLResponse)
async def web_app_knowledge_working_note(
    source_id: str,
    request: Request,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    session_id = _normalize_form_text(form.get("session_id"))
    working_note_value = _normalize_form_text(form.get("working_note")) or ""
    notice = (
        "当前页工作备注已人工确认；仅保留在当前返回页，不会写入 knowledge 主状态。"
        if working_note_value
        else "当前页工作备注已清空；不会写入 knowledge 主状态。"
    )
    completion_notice = (
        "当前页工作备注已人工提交，本轮 assistant 半手动链已结束。"
        if working_note_value
        else "当前页工作备注已清空，当前页已恢复默认状态。"
    )
    return _render_app_knowledge_detail_from_service(
        knowledge_service=knowledge_service,
        source_id=source_id,
        local_llm_service=local_llm_service,
        session_id=session_id,
        notice=notice,
        working_note_value=working_note_value,
        working_note_completion_notice=completion_notice,
    )


@router.get("/sessions/{session_id}/recap", response_class=HTMLResponse)
def web_app_recap(
    session_id: str,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    return _render_app_recap_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
    )


@router.post("/sessions/{session_id}/recap/assistant", response_class=HTMLResponse)
async def web_app_recap_assistant(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    selected_task, task_label = _assistant_task_selection(
        RECAP_ASSISTANT_TASKS,
        _normalize_form_text(form.get("assistant_task")),
    )
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Recap / Review",
            title="Recap 不可用",
            summary="当前无法加载 session recap。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Recap Missing",
            sidebar_html=_render_sidebar(active_section="recap"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
    ).model_dump(mode="json")
    assistant_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="session_recap",
        task_key=selected_task,
        task_label=task_label,
        context=_build_recap_assistant_context(snapshot=snapshot, context_pack=context_pack),
    )
    return _render_recap_page(
        session_id=session_id,
        snapshot=snapshot,
        context_pack=context_pack,
        assistant_result=assistant_result,
        selected_assistant_task=selected_task,
    )
