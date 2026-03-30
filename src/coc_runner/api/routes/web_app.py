from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from typing import Any, Callable, Mapping, TypedDict
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

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
    KeeperContextPack,
    KeeperWoundResolutionRequest,
    ReviewDraftRequest,
    SessionStatus,
    StartCombatContextRequest,
    UpdateKeeperPromptRequest,
    UpdateSessionLifecycleRequest,
    ViewerRole,
)
from coc_runner.domain.scenario_examples import (
    midnight_archive_payload,
    whispering_guesthouse_payload,
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
KEEPER_CONTEXT_PACK_ANCHOR_ID = "keeper-context-pack"
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
EXPERIMENTAL_AI_KP_DEMO_TASKS: dict[str, str] = {
    "demo_loop": "AI KP 剧情支架提案",
}
EXPERIMENTAL_AI_INVESTIGATOR_DEMO_TASKS: dict[str, str] = {
    "demo_loop": "AI Investigator 行动提案",
}
EXPERIMENTAL_AI_KEEPER_CONTINUITY_DRAFT_TASKS: dict[str, str] = {
    "draft_bridge": "Keeper continuity bridge 草稿",
}
EXPERIMENTAL_AI_VISIBLE_CONTINUITY_DRAFT_TASKS: dict[str, str] = {
    "draft_bridge": "Visible continuity bridge 草稿",
}
EXPERIMENTAL_DEMO_RUBRIC_FIELD_LABELS: dict[str, str] = {
    "kp_scene_coherence": "AI KP：scene framing 连贯性",
    "kp_pressure_reasonableness": "AI KP：pressure / next beat 合理性",
    "investigator_visible_fit": "AI investigator：是否符合 visible state",
    "investigator_action_value": "AI investigator：行动是否具体且有推进价值",
    "continuity_stability": "多轮 continuity 稳定性",
    "drift_or_leak_risk": "是否出现泄密 / 漂移 / 重复 / 空转",
}
EXPERIMENTAL_DEMO_RUBRIC_VALUE_LABELS: dict[str, str] = {
    "good": "好",
    "mixed": "一般",
    "poor": "差",
}
EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS = 6
EXPERIMENTAL_ONE_SHOT_MAX_TURNS_LIMIT = 10
EXPERIMENTAL_ONE_SHOT_SUCCESS_STREAK_TARGET = 3
EXPERIMENTAL_ONE_SHOT_STAGNATION_STREAK_LIMIT = 2
EXPERIMENTAL_ONE_SHOT_MISSING_CONTINUITY_STREAK_LIMIT = 2
EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS: dict[str, str] = {
    "success": "成功",
    "failure": "失败",
    "aborted": "中止",
    "max_turns": "达到轮数上限",
}
EXPERIMENTAL_ONE_SHOT_ENDING_REASON_LABELS: dict[str, str] = {
    "completed_demo_arc": "已形成连续、可读且带 continuity 的受控 demo mini-arc。",
    "stagnation_threshold": "连续多轮没有出现新的 run-local 推进点，判定为空转。",
    "missing_continuity_threshold": "连续多轮没有形成可用 continuity bridge，判定 demo run 失败。",
    "llm_unavailable": "实验块未返回可用结构化输出，当前 run 中止。",
    "visible_secret_breach": "visible-side 输出触碰 keeper-only 线索标题，当前 run 中止。",
    "turn_limit_reached": "达到当前受控 one-shot demo run 的最大轮数上限。",
}
EXPERIMENTAL_PRESET_ENDING_JUDGMENT_LABELS: dict[str, str] = {
    "decisive_success": "明确成功",
    "partial_success": "部分成功",
    "stalled_or_inconclusive": "停滞 / 未决",
    "collapse_or_failure": "崩坏 / 失败",
    "aborted": "中止",
}


@dataclass(slots=True)
class ExperimentalScenarioPresetEnding:
    preset_id: str
    judgment: str
    reason: str
    recap: str


@dataclass(frozen=True, slots=True)
class ExperimentalScenarioPresetEndingText:
    reason: str
    recap: str


@dataclass(frozen=True, slots=True)
class ExperimentalScenarioPresetVisibleSafeCues:
    decisive: tuple[str, ...]
    progress: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExperimentalScenarioPresetVisibleSafeEndingTexts:
    aborted_secret_breach: ExperimentalScenarioPresetEndingText
    aborted_default: ExperimentalScenarioPresetEndingText
    failure_stagnation: ExperimentalScenarioPresetEndingText
    failure_default: ExperimentalScenarioPresetEndingText
    max_turns_partial: ExperimentalScenarioPresetEndingText
    max_turns_stalled: ExperimentalScenarioPresetEndingText
    success_decisive: ExperimentalScenarioPresetEndingText
    success_partial: ExperimentalScenarioPresetEndingText
    success_stalled: ExperimentalScenarioPresetEndingText


@dataclass(frozen=True, slots=True)
class ExperimentalScenarioPresetJudgeConfig:
    preset_id: str
    label: str
    visible_safe_cues: ExperimentalScenarioPresetVisibleSafeCues
    visible_safe_endings: ExperimentalScenarioPresetVisibleSafeEndingTexts
    keeper_only_explanatory_text: str = ""


class ExperimentalScenarioPresetInternalDiagnostic(TypedDict):
    preset_id: str
    preset_label: str
    keeper_only_explanatory_text: str


class ExperimentalOneShotInternalAutopilotSeedContext(TypedDict):
    ending_status: str
    preset_id: str
    preset_label: str
    keeper_only_explanatory_text: str


class ExperimentalOneShotInternalAutopilotFollowUpHint(TypedDict):
    follow_up_kind: str
    preset_id: str
    preset_label: str
    keeper_anchor_text: str


class ExperimentalOneShotInternalAutopilotNextStepRecommendation(TypedDict):
    recommendation_kind: str
    preset_id: str
    preset_label: str
    recommended_focus_text: str


class ExperimentalOneShotInternalAutopilotMicroAction(TypedDict):
    action_kind: str
    preset_id: str
    preset_label: str
    action_text: str


class ExperimentalOneShotInternalAutopilotExecutionIntent(TypedDict):
    intent_kind: str
    preset_id: str
    preset_label: str
    intent_text: str


class ExperimentalOneShotInternalAutopilotExecutableStepPayload(TypedDict):
    payload_kind: str
    preset_id: str
    preset_label: str
    payload_text: str


class ExperimentalOneShotInternalAutopilotAgentInputEnvelope(TypedDict):
    envelope_kind: str
    preset_id: str
    preset_label: str
    envelope_text: str


class ExperimentalOneShotInternalAutopilotAgentTurnInput(TypedDict):
    turn_input_kind: str
    preset_id: str
    preset_label: str
    turn_input_text: str


class ExperimentalOneShotInternalAutopilotAgentTurnBrief(TypedDict):
    brief_kind: str
    preset_id: str
    preset_label: str
    brief_text: str


class ExperimentalOneShotInternalAutopilotAgentTurnNote(TypedDict):
    note_kind: str
    preset_id: str
    preset_label: str
    note_text: str


class ExperimentalOneShotInternalAutopilotAgentTurnMemo(TypedDict):
    memo_kind: str
    preset_id: str
    preset_label: str
    memo_text: str


class ExperimentalOneShotInternalAutopilotAgentMemoInput(TypedDict):
    input_kind: str
    preset_id: str
    preset_label: str
    input_text: str


class ExperimentalOneShotTurnFinalizedInternalSnapshot(TypedDict):
    kind: str
    text: str


class ExperimentalOneShotRecentTurnFinalizedSnapshotItem(TypedDict):
    turn_index: int
    status_label: str
    finalized_kind: str
    finalized_text: str
    stop_reason: str


@dataclass(slots=True)
class ExperimentalOneShotTurnRecord:
    turn_index: int
    kp_summary: str
    investigator_summary: str
    keeper_continuity: str
    visible_continuity: str
    narrative_work_note: str
    signature: str


@dataclass(slots=True)
class ExperimentalOneShotRunResult:
    ending_status: str
    ending_reason: str
    max_turns: int
    turn_records: list[ExperimentalOneShotTurnRecord]
    kp_result: LocalLLMAssistantResult | None
    investigator_result: LocalLLMAssistantResult | None
    keeper_draft_result: LocalLLMAssistantResult | None
    visible_draft_result: LocalLLMAssistantResult | None
    current_turn_index: int
    narrative_work_note_value: str
    keeper_turn_note_value: str
    visible_turn_note_value: str
    kp_turn_bridge: dict[str, Any] | None
    investigator_turn_bridge: dict[str, Any] | None
    keeper_draft_applied: bool
    visible_draft_applied: bool
    scenario_preset_ending: ExperimentalScenarioPresetEnding | None = None
    scenario_preset_internal_diagnostic: (
        ExperimentalScenarioPresetInternalDiagnostic | None
    ) = None
    scenario_preset_internal_diagnostic_json: str = ""
    error_message: str = ""
    secret_breach_term: str = ""


@dataclass(slots=True)
class ExperimentalAutopilotTokenSurface:
    phase: str
    badge_label: str
    badge_tone: str
    status_text: str
    detail_text: str
    cancel_like_text: str
    stop_reason_text: str = ""
    runtime_text: str = ""


@dataclass(slots=True)
class ExperimentalAutopilotLastRunRecall:
    ending_status: str
    ending_reason: str
    provider_name: str = ""
    model: str = ""


@dataclass(slots=True)
class ExperimentalAutopilotRuntimeCopy:
    status_text: str
    stop_reason_text: str = ""
    runtime_text: str = ""


EXPERIMENTAL_ONE_SHOT_VISIBLE_SAFE_FORBIDDEN_MARKERS: tuple[str, ...] = (
    "private_notes",
    "secret_state_refs",
    "keeper_workflow",
)


EXPERIMENTAL_ONE_SHOT_PRESET_ENDING_CONFIGS: dict[
    str,
    ExperimentalScenarioPresetJudgeConfig,
] = {
    "scenario.whispering_guesthouse": ExperimentalScenarioPresetJudgeConfig(
        preset_id="scenario.whispering_guesthouse",
        label="雾港旅店的低语",
        visible_safe_cues=ExperimentalScenarioPresetVisibleSafeCues(
            decisive=(
                "地窖门前异味",
                "封死地窖门",
                "地窖入口",
                "地窖门",
                "门槛",
                "异味",
            ),
            progress=(
                "204 房",
                "204房",
                "账册",
                "缺页",
                "登记",
                "二楼脚步声",
                "二楼",
            ),
        ),
        visible_safe_endings=ExperimentalScenarioPresetVisibleSafeEndingTexts(
            aborted_secret_breach=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 因公开侧触碰 keeper-only 禁区而被保护性中止，不能继续把这次输出解释成场景结局。",
                recap="这次雾港旅店 demo 在形成稳定收尾前就触发了 secret boundary；当前 transcript 只保留为实验记录。",
            ),
            aborted_default=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 没有拿到可用实验输出，未能形成足够的调查推进，因此只能按中止处理。",
                recap="这次雾港旅店 demo 在形成稳定调查弧线前就已中止，没有得到可解释的场景收尾。",
            ),
            failure_stagnation=ExperimentalScenarioPresetEndingText(
                reason="调查一直围绕账房记录、缺页与老板回避打转，没有把压力继续推进到更明确的异常入口，因此按停滞 / 未决收尾。",
                recap="这次雾港旅店 demo 反复停在账册缺页与老板回避周围，没有真正把收尾推进到地窖入口层级。",
            ),
            failure_default=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 没能维持可解释的 continuity bridge，preset 下视为这次调查弧线已经崩坏。",
                recap="这次雾港旅店 demo 没能维持住调查推进，最后只留下一个崩坏 / 失败的实验收尾。",
            ),
            max_turns_partial=ExperimentalScenarioPresetEndingText(
                reason="调查已经把旅店疑点推进到账房记录、204 房或更深一层的异常，但在轮数上限前没有完成更明确的收束，因此按部分成功解释。",
                recap="这次雾港旅店 demo 已经把疑点从账房推进到旅店异常链上，但仍在真正收尾前被轮数上限截住。",
            ),
            max_turns_stalled=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 达到轮数上限时仍没有形成足够的调查推进，因此只能按未决收尾解释。",
                recap="这次雾港旅店 demo 在达到最大轮数后停下，留下的是未决而非完成的场景收尾。",
            ),
            success_decisive=ExperimentalScenarioPresetEndingText(
                reason="run 已从账房记录推进到地窖入口级别的异常，并保持连续 continuity，当前 preset 下可视为一次明确成功的 demo 收尾。",
                recap="这次雾港旅店 demo 最终从账房缺页和 204 房异常一路推进到地窖门前异味，形成了一个足以指向封死地窖入口的收尾。",
            ),
            success_partial=ExperimentalScenarioPresetEndingText(
                reason="run 虽形成了连续 mini-arc，但主要停留在账房记录、204 房与旅店动静这一层，因此按部分成功解释。",
                recap="这次雾港旅店 demo 已把调查推进到账房记录与旅店异常，但还没有真正触到更深一层的地窖入口收尾。",
            ),
            success_stalled=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 虽已结束，但 transcript 中没有足够的 preset 进展锚点，只能按未决解释。",
                recap="这次雾港旅店 demo 虽然跑完了，但没有形成足够清晰的场景收尾锚点。",
            ),
        ),
        keeper_only_explanatory_text=(
            "Keeper 内部说明：可把“旅店旧图纸”“储物间账本残页”“地窖门槛符号”"
            "视作旅店调查弧线的内部锚点；visible 侧只应落到账册缺页、204 房异常与"
            "地窖门前异味等外显表述。"
        ),
    ),
    "scenario.midnight_archive": ExperimentalScenarioPresetJudgeConfig(
        preset_id="scenario.midnight_archive",
        label="雨夜档案馆",
        visible_safe_cues=ExperimentalScenarioPresetVisibleSafeCues(
            decisive=(
                "灼热擦痕",
                "扶手余温",
                "余温",
                "焦味",
                "金属摩擦声",
                "滚烫金属",
            ),
            progress=(
                "借阅目录",
                "烧焦的便条",
                "守夜人",
                "地下楼梯间",
                "阅览室",
            ),
        ),
        visible_safe_endings=ExperimentalScenarioPresetVisibleSafeEndingTexts(
            aborted_secret_breach=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 因公开侧触碰 keeper-only 禁区而被保护性中止，不能继续把这次档案馆输出解释成场景结局。",
                recap="这次雨夜档案馆 demo 在形成稳定收尾前就触发了 secret boundary；当前 transcript 只保留为实验记录。",
            ),
            aborted_default=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 没有拿到可用实验输出，未能把阅览室线索推进成可解释的档案馆收尾，因此只能按中止处理。",
                recap="这次雨夜档案馆 demo 在形成稳定调查弧线前就已中止，没有得到可解释的场景收尾。",
            ),
            failure_stagnation=ExperimentalScenarioPresetEndingText(
                reason="调查一直围绕借阅目录与守夜人的回避打转，没有把压力继续推进到地下楼梯间异常，因此按停滞 / 未决收尾。",
                recap="这次雨夜档案馆 demo 反复停在阅览室目录与守夜人口供周围，没有真正把收尾推进到楼梯间的灼热擦痕与余温层级。",
            ),
            failure_default=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 没能维持可解释的 continuity bridge，preset 下视为这次档案馆调查弧线已经崩坏。",
                recap="这次雨夜档案馆 demo 没能维持住调查推进，最后只留下一个崩坏 / 失败的实验收尾。",
            ),
            max_turns_partial=ExperimentalScenarioPresetEndingText(
                reason="调查已经把档案馆疑点推进到借阅目录、守夜人口供或地下楼梯间异常，但在轮数上限前没有完成更明确的收束，因此按部分成功解释。",
                recap="这次雨夜档案馆 demo 已经把疑点从阅览室推进到地下楼梯间线索上，但仍在真正收尾前被轮数上限截住。",
            ),
            max_turns_stalled=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 达到轮数上限时仍没有形成足够的档案馆推进，因此只能按未决收尾解释。",
                recap="这次雨夜档案馆 demo 在达到最大轮数后停下，留下的是未决而非完成的场景收尾。",
            ),
            success_decisive=ExperimentalScenarioPresetEndingText(
                reason="run 已从阅览室目录推进到地下楼梯间的灼热擦痕、余温或金属摩擦声异常，并保持连续 continuity，当前 preset 下可视为一次明确成功的 demo 收尾。",
                recap="这次雨夜档案馆 demo 最终从借阅目录与守夜人口供一路推进到楼梯间的灼热擦痕和扶手余温，形成了一个足以指向地下异常入口的收尾。",
            ),
            success_partial=ExperimentalScenarioPresetEndingText(
                reason="run 虽形成了连续 mini-arc，但主要停留在阅览室目录、守夜人口供与地下楼梯间入口这一层，因此按部分成功解释。",
                recap="这次雨夜档案馆 demo 已把调查推进到档案馆的夜间借阅与楼梯间异常，但还没有真正触到更明确的危险收尾。",
            ),
            success_stalled=ExperimentalScenarioPresetEndingText(
                reason="当前 demo run 虽已结束，但 transcript 中没有足够的档案馆 preset 进展锚点，只能按未决解释。",
                recap="这次雨夜档案馆 demo 虽然跑完了，但没有形成足够清晰的场景收尾锚点。",
            ),
        ),
        keeper_only_explanatory_text=(
            "Keeper 内部说明：可把“烧焦便笺”“楼梯灼痕”视作档案馆调查弧线的内部锚点；"
            "visible 侧只应落到借阅目录、守夜人口供、扶手余温与焦味等外显表述。"
        ),
    ),
}

EXPERIMENTAL_ONE_SHOT_PRESET_SCENARIO_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "scenario.whispering_guesthouse": whispering_guesthouse_payload,
    "scenario.midnight_archive": midnight_archive_payload,
}


def _build_experimental_visible_safe_config_forbidden_terms(
    *,
    scenario_payload: Mapping[str, Any],
) -> tuple[str, ...]:
    terms = list(EXPERIMENTAL_ONE_SHOT_VISIBLE_SAFE_FORBIDDEN_MARKERS)
    for clue in scenario_payload.get("clues") or []:
        if not isinstance(clue, dict):
            continue
        title = _normalize_form_text(clue.get("title")) or ""
        scope = _normalize_form_text(clue.get("visibility_scope")) or ""
        if title and scope in {"kp_only", "hidden_clue", "system_internal"}:
            terms.append(title)
    return tuple(dict.fromkeys(term for term in terms if term))


def _iter_experimental_visible_safe_config_texts(
    config: ExperimentalScenarioPresetJudgeConfig,
) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    items.extend(
        (f"{config.preset_id}.visible_safe_cues.decisive", cue)
        for cue in config.visible_safe_cues.decisive
    )
    items.extend(
        (f"{config.preset_id}.visible_safe_cues.progress", cue)
        for cue in config.visible_safe_cues.progress
    )
    ending_fields = {
        "aborted_secret_breach": config.visible_safe_endings.aborted_secret_breach,
        "aborted_default": config.visible_safe_endings.aborted_default,
        "failure_stagnation": config.visible_safe_endings.failure_stagnation,
        "failure_default": config.visible_safe_endings.failure_default,
        "max_turns_partial": config.visible_safe_endings.max_turns_partial,
        "max_turns_stalled": config.visible_safe_endings.max_turns_stalled,
        "success_decisive": config.visible_safe_endings.success_decisive,
        "success_partial": config.visible_safe_endings.success_partial,
        "success_stalled": config.visible_safe_endings.success_stalled,
    }
    for field_name, ending_text in ending_fields.items():
        items.append(
            (
                f"{config.preset_id}.visible_safe_endings.{field_name}.reason",
                ending_text.reason,
            )
        )
        items.append(
            (
                f"{config.preset_id}.visible_safe_endings.{field_name}.recap",
                ending_text.recap,
            )
        )
    return items


def _lint_experimental_visible_safe_preset_config(
    *,
    config: ExperimentalScenarioPresetJudgeConfig,
    forbidden_terms: tuple[str, ...],
) -> None:
    violations: list[str] = []
    for field_name, text in _iter_experimental_visible_safe_config_texts(config):
        for term in forbidden_terms:
            if term and term in text:
                violations.append(f"{field_name} 命中不可见词 `{term}`")
    if violations:
        details = "；".join(violations)
        raise ValueError(
            f"experimental one-shot preset config visible-safe lint failed for {config.preset_id}: {details}"
        )


def _validate_experimental_one_shot_preset_configs_visible_safe() -> None:
    for preset_id, config in EXPERIMENTAL_ONE_SHOT_PRESET_ENDING_CONFIGS.items():
        scenario_builder = EXPERIMENTAL_ONE_SHOT_PRESET_SCENARIO_BUILDERS.get(preset_id)
        if scenario_builder is None:
            raise ValueError(
                f"experimental one-shot preset config missing scenario builder for {preset_id}"
            )
        forbidden_terms = _build_experimental_visible_safe_config_forbidden_terms(
            scenario_payload=scenario_builder()
        )
        _lint_experimental_visible_safe_preset_config(
            config=config,
            forbidden_terms=forbidden_terms,
        )


_validate_experimental_one_shot_preset_configs_visible_safe()


def _experimental_ai_demo_narrative_work_note_target_id(session_id: str) -> str:
    return f"experimental-narrative-work-note-{session_id}"


def _experimental_ai_demo_keeper_continuity_target_id(session_id: str) -> str:
    return f"experimental-keeper-turn-outcome-note-{session_id}"


def _experimental_ai_demo_visible_continuity_target_id(session_id: str) -> str:
    return f"experimental-visible-turn-outcome-note-{session_id}"


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


def _experimental_preview_handoff_fallback_text(
    result: LocalLLMAssistantResult,
) -> str | None:
    assistant = result.assistant
    if assistant is None:
        return None
    lines: list[str] = []
    summary = _normalize_form_text(assistant.summary)
    if summary:
        lines.append(summary)
    bullets = [item for item in assistant.bullets if item][:3]
    if bullets:
        lines.extend(f"- {item}" for item in bullets)
    return "\n".join(lines).strip() or None


def _render_experimental_preview_handoff(
    *,
    session_id: str,
    step_key: str,
    result: LocalLLMAssistantResult | None,
    button_label: str,
    target_id: str,
    helper_text: str,
    adopted_status_text: str,
) -> str:
    if result is None or result.status != "success" or result.assistant is None:
        return ""
    draft_text = _normalize_form_text(
        result.assistant.draft_text
    ) or _experimental_preview_handoff_fallback_text(result)
    if not draft_text:
        return ""
    source_id = f"experimental-preview-handoff-source-{step_key}-{session_id}"
    status_id = f"experimental-preview-handoff-status-{step_key}-{session_id}"
    return f"""
      <textarea id="{escape(source_id)}" class="assistant-draft-source" aria-hidden="true" tabindex="-1">{escape(draft_text)}</textarea>
      <div class="adoption-toolbar">
        <button
          class="button-button ghost"
          type="button"
          data-adopt-source="{escape(source_id)}"
          data-adopt-target="{escape(target_id)}"
          data-adopt-status="{escape(status_id)}"
          data-adopt-status-text="{escape(adopted_status_text, quote=True)}"
        >{escape(button_label)}</button>
      </div>
      <p id="{escape(status_id)}" class="helper adoption-status">{escape(helper_text)}</p>
    """


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
    compressed_context: dict[str, Any] | None = None,
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
    if compressed_context:
        context["compressed_context"] = compressed_context
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
    compressed_context: dict[str, Any] | None = None,
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
    if compressed_context:
        context["compressed_context"] = compressed_context
    return context


def _demo_investigator_candidates(
    participants: list[dict[str, Any]],
    *,
    keeper_id: str | None = None,
) -> list[dict[str, Any]]:
    return [
        participant
        for participant in participants
        if isinstance(participant, dict)
        and participant.get("actor_id") != keeper_id
        and participant.get("kind") in {"human", "ai"}
    ]


def _build_experimental_ai_kp_demo_context(
    *,
    snapshot: dict[str, Any],
    context_pack: dict[str, Any],
    compressed_context: dict[str, Any],
    turn_bridge: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "compressed_context": compressed_context,
        "recent_event_lines": list(context_pack.get("recent_event_lines") or [])[:3],
        "guardrail": {
            "mode": "experimental_demo",
            "non_authoritative": True,
            "auto_advance_allowed": False,
        },
    }
    if turn_bridge:
        context["turn_bridge"] = turn_bridge
    return context


def _build_experimental_ai_investigator_demo_context(
    *,
    viewer_id: str,
    view: dict[str, Any],
    turn_bridge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    participants = [
        participant
        for participant in view.get("participants") or []
        if isinstance(participant, dict)
    ]
    participant = _participant_map(participants).get(viewer_id) or {}
    own_state = (
        view.get("own_character_state")
        if isinstance(view.get("own_character_state"), dict)
        else {}
    )
    current_scene = view.get("current_scene") or {}
    combat_context = view.get("combat_context") or {}
    status_flags = [
        label
        for flag, label in [
            ("heavy_wound_active", "重伤"),
            ("is_unconscious", "昏迷"),
            ("is_dying", "濒死"),
            ("is_stable", "已稳定"),
            ("rescue_window_open", "短时可救"),
            ("death_confirmed", "已死亡"),
        ]
        if own_state.get(flag)
    ]
    visible_clues = [
        clue
        for clue in ((view.get("scenario") or {}).get("clues") or [])
        if isinstance(clue, dict)
    ]
    context = {
        "viewer": {
            "actor_id": viewer_id,
            "display_name": participant.get("display_name"),
            "kind": participant.get("kind"),
            "occupation": (participant.get("character") or {}).get("occupation"),
        },
        "session": {
            "session_id": view.get("session_id"),
            "scenario_title": (view.get("scenario") or {}).get("title"),
            "status": view.get("session_status") or view.get("status"),
            "current_scene": current_scene.get("title"),
            "current_scene_summary": _excerpt(current_scene.get("summary"), limit=140),
        },
        "visible_clues": [
            {
                "title": clue.get("title"),
                "summary": _excerpt(clue.get("text"), limit=120),
            }
            for clue in visible_clues[:3]
        ],
        "recent_events": _event_excerpt_items(
            list(reversed(view.get("visible_events") or [])),
            limit=4,
        ),
        "own_state": {
            "current_hit_points": own_state.get("current_hit_points"),
            "current_magic_points": own_state.get("current_magic_points"),
            "current_sanity": own_state.get("current_sanity"),
            "status_flags": status_flags,
        },
        "combat": {
            "current_actor_id": combat_context.get("current_actor_id"),
            "round_number": combat_context.get("round_number"),
            "turn_order_count": len(combat_context.get("turn_order") or []),
        },
        "guardrail": {
            "mode": "experimental_demo",
            "non_authoritative": True,
            "keeper_only_fields_included": False,
        },
    }
    if turn_bridge:
        context["turn_bridge"] = turn_bridge
    return context


def _build_experimental_demo_evaluation_hint(
    evaluation_state: Mapping[str, str] | None,
) -> dict[str, Any] | None:
    if not evaluation_state:
        return None
    label = _normalize_form_text(evaluation_state.get("evaluation_label")) or ""
    note = _normalize_form_text(evaluation_state.get("evaluation_note")) or ""
    if not label and not note:
        return None
    payload: dict[str, Any] = {}
    if label:
        payload["label"] = label
    if note:
        payload["note"] = note
    return payload or None


def _build_experimental_keeper_continuity_draft_context(
    *,
    snapshot: dict[str, Any],
    compressed_context: dict[str, Any],
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    evaluation_state: Mapping[str, str] | None = None,
) -> dict[str, Any]:
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
        "compressed_context": compressed_context,
        "current_ai_kp_output": _assistant_result_turn_bridge_payload(kp_result),
        "current_ai_investigator_output": _assistant_result_turn_bridge_payload(
            investigator_result
        ),
        "guardrail": {
            "mode": "experimental_demo_continuity_draft",
            "non_authoritative": True,
            "page_local_only": True,
            "auto_advance_allowed": False,
        },
    }
    evaluation_hint = _build_experimental_demo_evaluation_hint(evaluation_state)
    if evaluation_hint:
        context["evaluation_hint"] = evaluation_hint
    return context


def _build_experimental_visible_continuity_draft_context(
    *,
    viewer_id: str,
    view: dict[str, Any],
    investigator_result: LocalLLMAssistantResult | None,
) -> dict[str, Any]:
    context = _build_experimental_ai_investigator_demo_context(
        viewer_id=viewer_id,
        view=view,
    )
    context["current_ai_investigator_output"] = _assistant_result_turn_bridge_payload(
        investigator_result
    )
    context["guardrail"] = {
        "mode": "experimental_demo_visible_continuity_draft",
        "non_authoritative": True,
        "page_local_only": True,
        "keeper_only_fields_included": False,
    }
    return context


def _assistant_result_turn_bridge_payload(
    result: LocalLLMAssistantResult | None,
) -> dict[str, str] | None:
    if result is None or result.status != "success" or result.assistant is None:
        return None
    assistant = result.assistant
    payload = {
        "title": _normalize_form_text(assistant.title) or _normalize_form_text(result.task_label) or "",
        "summary": _normalize_form_text(assistant.summary) or "",
        "draft_excerpt": _excerpt(
            _normalize_form_text(assistant.draft_text),
            limit=180,
        )
        or "",
    }
    if not any(payload.values()):
        return None
    return payload


def _turn_bridge_payload_from_form(
    form: Mapping[str, Any],
    *,
    prefix: str,
) -> dict[str, str] | None:
    payload = {
        "title": _normalize_form_text(form.get(f"{prefix}_title")) or "",
        "summary": _normalize_form_text(form.get(f"{prefix}_summary")) or "",
        "draft_excerpt": _normalize_form_text(form.get(f"{prefix}_draft_excerpt")) or "",
    }
    if not any(payload.values()):
        return None
    return payload


def _parse_turn_index(raw_value: Any) -> int:
    try:
        value = int(str(raw_value or "0"))
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


def _build_experimental_ai_kp_turn_bridge(
    *,
    previous_turn_index: int,
    prior_kp_payload: dict[str, str] | None,
    prior_investigator_payload: dict[str, str] | None,
    keeper_turn_note: str,
    visible_turn_note: str,
) -> dict[str, Any] | None:
    bridge: dict[str, Any] = {
        "mode": "page_local_temporary_turn_bridge",
        "non_authoritative": True,
    }
    if previous_turn_index > 0:
        bridge["previous_turn_index"] = previous_turn_index
    if prior_kp_payload:
        bridge["previous_ai_kp"] = prior_kp_payload
    if prior_investigator_payload:
        bridge["previous_ai_investigator"] = prior_investigator_payload
    if keeper_turn_note:
        bridge["keeper_adoption_and_outcome_note"] = keeper_turn_note
    if visible_turn_note:
        bridge["public_outcome_note"] = visible_turn_note
    if len(bridge) <= 2:
        return None
    return bridge


def _build_experimental_ai_investigator_turn_bridge(
    *,
    previous_turn_index: int,
    prior_investigator_payload: dict[str, str] | None,
    visible_turn_note: str,
) -> dict[str, Any] | None:
    bridge: dict[str, Any] = {
        "mode": "page_local_temporary_visible_turn_bridge",
        "visible_only": True,
        "non_authoritative": True,
    }
    if previous_turn_index > 0:
        bridge["previous_turn_index"] = previous_turn_index
    if prior_investigator_payload:
        bridge["previous_ai_investigator"] = prior_investigator_payload
    if visible_turn_note:
        bridge["public_outcome_note"] = visible_turn_note
    if len(bridge) <= 3:
        return None
    return bridge


def _parse_one_shot_max_turns(raw_value: Any) -> int:
    try:
        value = int(str(raw_value or EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS))
    except (TypeError, ValueError):
        return EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS
    return max(1, min(value, EXPERIMENTAL_ONE_SHOT_MAX_TURNS_LIMIT))


def _assistant_summary_text(result: LocalLLMAssistantResult | None) -> str:
    if result is None or result.status != "success" or result.assistant is None:
        return ""
    return _normalize_form_text(result.assistant.summary) or ""


def _assistant_draft_text(result: LocalLLMAssistantResult | None) -> str:
    if result is None or result.status != "success" or result.assistant is None:
        return ""
    return _normalize_form_text(result.assistant.draft_text) or ""


def _assistant_combined_text(result: LocalLLMAssistantResult | None) -> str:
    if result is None or result.assistant is None:
        return ""
    assistant = result.assistant
    parts = [
        _normalize_form_text(assistant.title) or "",
        _normalize_form_text(assistant.summary) or "",
        _normalize_form_text(assistant.draft_text) or "",
        *[
            _normalize_form_text(item) or ""
            for item in list(assistant.bullets or []) + list(assistant.suggested_questions or [])
        ],
    ]
    return " ".join(part for part in parts if part)


def _run_experimental_self_play_chain_turn(
    *,
    local_llm_service: LocalLLMService,
    snapshot: dict[str, Any],
    context_pack: dict[str, Any],
    compressed_context: dict[str, Any],
    viewer_id: str,
    investigator_view: dict[str, Any],
    previous_turn_index: int,
    previous_kp_payload: dict[str, str] | None,
    previous_investigator_payload: dict[str, str] | None,
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    evaluation_state: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    kp_turn_bridge = _build_experimental_ai_kp_turn_bridge(
        previous_turn_index=previous_turn_index,
        prior_kp_payload=previous_kp_payload,
        prior_investigator_payload=previous_investigator_payload,
        keeper_turn_note=keeper_turn_note_value,
        visible_turn_note=visible_turn_note_value,
    )
    investigator_turn_bridge = _build_experimental_ai_investigator_turn_bridge(
        previous_turn_index=previous_turn_index,
        prior_investigator_payload=previous_investigator_payload,
        visible_turn_note=visible_turn_note_value,
    )
    next_turn_index = previous_turn_index + 1
    kp_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_kp_demo",
        task_key="demo_loop",
        task_label=EXPERIMENTAL_AI_KP_DEMO_TASKS["demo_loop"],
        context=_build_experimental_ai_kp_demo_context(
            snapshot=snapshot,
            context_pack=context_pack,
            compressed_context=compressed_context,
            turn_bridge=kp_turn_bridge,
        ),
    )
    investigator_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_investigator_demo",
        task_key="demo_loop",
        task_label=EXPERIMENTAL_AI_INVESTIGATOR_DEMO_TASKS["demo_loop"],
        context=_build_experimental_ai_investigator_demo_context(
            viewer_id=viewer_id,
            view=investigator_view,
            turn_bridge=investigator_turn_bridge,
        ),
    )
    keeper_draft_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_keeper_continuity_draft",
        task_key="draft_bridge",
        task_label=EXPERIMENTAL_AI_KEEPER_CONTINUITY_DRAFT_TASKS["draft_bridge"],
        context=_build_experimental_keeper_continuity_draft_context(
            snapshot=snapshot,
            compressed_context=compressed_context,
            kp_result=kp_result,
            investigator_result=investigator_result,
            evaluation_state=evaluation_state,
        ),
    )
    visible_draft_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_visible_continuity_draft",
        task_key="draft_bridge",
        task_label=EXPERIMENTAL_AI_VISIBLE_CONTINUITY_DRAFT_TASKS["draft_bridge"],
        context=_build_experimental_visible_continuity_draft_context(
            viewer_id=viewer_id,
            view=investigator_view,
            investigator_result=investigator_result,
        ),
    )
    kp_payload = _assistant_result_turn_bridge_payload(kp_result)
    investigator_payload = _assistant_result_turn_bridge_payload(investigator_result)
    kp_draft_text = _assistant_draft_text(kp_result)
    next_keeper_turn_note_value = _assistant_draft_text(keeper_draft_result) or keeper_turn_note_value
    next_visible_turn_note_value = _assistant_draft_text(visible_draft_result) or visible_turn_note_value
    keeper_draft_applied = bool(_assistant_draft_text(keeper_draft_result))
    visible_draft_applied = bool(_assistant_draft_text(visible_draft_result))
    return {
        "next_turn_index": next_turn_index,
        "kp_turn_bridge": kp_turn_bridge,
        "investigator_turn_bridge": investigator_turn_bridge,
        "kp_result": kp_result,
        "investigator_result": investigator_result,
        "keeper_draft_result": keeper_draft_result,
        "visible_draft_result": visible_draft_result,
        "kp_payload": kp_payload,
        "investigator_payload": investigator_payload,
        "kp_draft_text": kp_draft_text,
        "keeper_turn_note_value": next_keeper_turn_note_value,
        "visible_turn_note_value": next_visible_turn_note_value,
        "keeper_draft_applied": keeper_draft_applied,
        "visible_draft_applied": visible_draft_applied,
    }


def _build_experimental_one_shot_forbidden_visible_terms(
    *,
    snapshot: dict[str, Any],
    investigator_view: dict[str, Any],
) -> list[str]:
    keeper_clues = [
        clue
        for clue in ((snapshot.get("scenario") or {}).get("clues") or [])
        if isinstance(clue, dict)
    ]
    visible_clue_titles = {
        _normalize_form_text(clue.get("title")) or ""
        for clue in (((investigator_view.get("scenario") or {}).get("clues") or []))
        if isinstance(clue, dict)
    }
    terms: list[str] = ["private_notes", "secret_state_refs", "keeper_workflow"]
    for clue in keeper_clues:
        title = _normalize_form_text(clue.get("title")) or ""
        if title and title not in visible_clue_titles:
            terms.append(title)
    return list(dict.fromkeys(term for term in terms if term))


def _find_experimental_visible_secret_breach(
    *,
    investigator_result: LocalLLMAssistantResult | None,
    visible_draft_result: LocalLLMAssistantResult | None,
    forbidden_terms: list[str],
) -> str:
    candidate_text = " ".join(
        text
        for text in [
            _assistant_combined_text(investigator_result),
            _assistant_combined_text(visible_draft_result),
        ]
        if text
    )
    for term in forbidden_terms:
        if term and term in candidate_text:
            return term
    return ""


def _build_experimental_one_shot_turn_signature(
    *,
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    keeper_turn_note_value: str,
    visible_turn_note_value: str,
) -> str:
    signature_parts = [
        _assistant_summary_text(kp_result),
        _assistant_summary_text(investigator_result),
        _normalize_form_text(keeper_turn_note_value) or "",
        _normalize_form_text(visible_turn_note_value) or "",
    ]
    return " | ".join(part for part in signature_parts if part)


def _build_experimental_one_shot_turn_record(
    *,
    turn_index: int,
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    keeper_turn_note_value: str,
    visible_turn_note_value: str,
    narrative_work_note_value: str,
    signature: str,
) -> ExperimentalOneShotTurnRecord:
    return ExperimentalOneShotTurnRecord(
        turn_index=turn_index,
        kp_summary=_assistant_summary_text(kp_result),
        investigator_summary=_assistant_summary_text(investigator_result),
        keeper_continuity=_normalize_form_text(keeper_turn_note_value) or "",
        visible_continuity=_normalize_form_text(visible_turn_note_value) or "",
        narrative_work_note=_normalize_form_text(narrative_work_note_value) or "",
        signature=signature,
    )


def _run_experimental_one_shot_demo(
    *,
    service: SessionService,
    local_llm_service: LocalLLMService,
    session: Any,
    keeper_view: Any,
    snapshot: dict[str, Any],
    investigator_view: dict[str, Any],
    investigator_id: str,
    max_turns: int,
    evaluation_state: Mapping[str, str] | None = None,
    initial_narrative_work_note_value: str = "",
    initial_keeper_turn_note_value: str = "",
    initial_visible_turn_note_value: str = "",
) -> ExperimentalOneShotRunResult:
    runtime_assistance = service.get_keeper_runtime_assistance(keeper_view=keeper_view)
    forbidden_terms = _build_experimental_one_shot_forbidden_visible_terms(
        snapshot=snapshot,
        investigator_view=investigator_view,
    )
    turn_records: list[ExperimentalOneShotTurnRecord] = []
    previous_kp_payload: dict[str, str] | None = None
    previous_investigator_payload: dict[str, str] | None = None
    current_turn_index = 0
    narrative_work_note_value = initial_narrative_work_note_value
    keeper_turn_note_value = initial_keeper_turn_note_value
    visible_turn_note_value = initial_visible_turn_note_value
    successful_turn_streak = 0
    stagnation_streak = 0
    missing_continuity_streak = 0
    previous_signature = ""
    last_turn: dict[str, Any] = {
        "kp_result": None,
        "investigator_result": None,
        "keeper_draft_result": None,
        "visible_draft_result": None,
        "kp_turn_bridge": None,
        "investigator_turn_bridge": None,
        "keeper_draft_applied": False,
        "visible_draft_applied": False,
    }

    for _ in range(max_turns):
        context_pack = _build_keeper_context_pack_payload(
            service=service,
            session=session,
            keeper_view=keeper_view,
            runtime_assistance=runtime_assistance,
            narrative_note_value=narrative_work_note_value,
        )
        compressed_context = _build_keeper_compressed_context_payload(
            service=service,
            context_pack=context_pack,
        )
        turn = _run_experimental_self_play_chain_turn(
            local_llm_service=local_llm_service,
            snapshot=snapshot,
            context_pack=context_pack,
            compressed_context=compressed_context,
            viewer_id=investigator_id,
            investigator_view=investigator_view,
            previous_turn_index=current_turn_index,
            previous_kp_payload=previous_kp_payload,
            previous_investigator_payload=previous_investigator_payload,
            keeper_turn_note_value=keeper_turn_note_value,
            visible_turn_note_value=visible_turn_note_value,
            evaluation_state=evaluation_state,
        )
        current_turn_index = int(turn["next_turn_index"])
        kp_result = turn["kp_result"]
        investigator_result = turn["investigator_result"]
        keeper_draft_result = turn["keeper_draft_result"]
        visible_draft_result = turn["visible_draft_result"]
        keeper_turn_note_value = turn["keeper_turn_note_value"]
        visible_turn_note_value = turn["visible_turn_note_value"]
        if turn["kp_draft_text"]:
            narrative_work_note_value = turn["kp_draft_text"]
        signature = _build_experimental_one_shot_turn_signature(
            kp_result=kp_result,
            investigator_result=investigator_result,
            keeper_turn_note_value=keeper_turn_note_value,
            visible_turn_note_value=visible_turn_note_value,
        )
        turn_records.append(
            _build_experimental_one_shot_turn_record(
                turn_index=current_turn_index,
                kp_result=kp_result,
                investigator_result=investigator_result,
                keeper_turn_note_value=keeper_turn_note_value,
                visible_turn_note_value=visible_turn_note_value,
                narrative_work_note_value=narrative_work_note_value,
                signature=signature,
            )
        )
        last_turn = turn
        if any(
            result is None or result.status != "success" or result.assistant is None
            for result in [
                kp_result,
                investigator_result,
                keeper_draft_result,
                visible_draft_result,
            ]
        ):
            error_message = next(
                (
                    result.error_message or ""
                    for result in [
                        kp_result,
                        investigator_result,
                        keeper_draft_result,
                        visible_draft_result,
                    ]
                    if result is not None and result.status != "success"
                ),
                "",
            )
            return ExperimentalOneShotRunResult(
                ending_status="aborted",
                ending_reason="llm_unavailable",
                max_turns=max_turns,
                turn_records=turn_records,
                kp_result=kp_result,
                investigator_result=investigator_result,
                keeper_draft_result=keeper_draft_result,
                visible_draft_result=visible_draft_result,
                current_turn_index=current_turn_index,
                narrative_work_note_value=narrative_work_note_value,
                keeper_turn_note_value=keeper_turn_note_value,
                visible_turn_note_value=visible_turn_note_value,
                kp_turn_bridge=turn["kp_turn_bridge"],
                investigator_turn_bridge=turn["investigator_turn_bridge"],
                keeper_draft_applied=bool(turn["keeper_draft_applied"]),
                visible_draft_applied=bool(turn["visible_draft_applied"]),
                error_message=error_message,
            )
        secret_breach_term = _find_experimental_visible_secret_breach(
            investigator_result=investigator_result,
            visible_draft_result=visible_draft_result,
            forbidden_terms=forbidden_terms,
        )
        if secret_breach_term:
            return ExperimentalOneShotRunResult(
                ending_status="aborted",
                ending_reason="visible_secret_breach",
                max_turns=max_turns,
                turn_records=turn_records,
                kp_result=kp_result,
                investigator_result=investigator_result,
                keeper_draft_result=keeper_draft_result,
                visible_draft_result=visible_draft_result,
                current_turn_index=current_turn_index,
                narrative_work_note_value=narrative_work_note_value,
                keeper_turn_note_value=keeper_turn_note_value,
                visible_turn_note_value=visible_turn_note_value,
                kp_turn_bridge=turn["kp_turn_bridge"],
                investigator_turn_bridge=turn["investigator_turn_bridge"],
                keeper_draft_applied=bool(turn["keeper_draft_applied"]),
                visible_draft_applied=bool(turn["visible_draft_applied"]),
                secret_breach_term=secret_breach_term,
            )
        if signature and signature == previous_signature:
            stagnation_streak += 1
        else:
            stagnation_streak = 0
        previous_signature = signature or previous_signature
        if turn["keeper_draft_applied"] and turn["visible_draft_applied"]:
            missing_continuity_streak = 0
        else:
            missing_continuity_streak += 1
        if (
            _assistant_summary_text(kp_result)
            and _assistant_summary_text(investigator_result)
            and turn["keeper_draft_applied"]
            and turn["visible_draft_applied"]
            and stagnation_streak == 0
        ):
            successful_turn_streak += 1
        else:
            successful_turn_streak = 0
        previous_kp_payload = turn["kp_payload"]
        previous_investigator_payload = turn["investigator_payload"]
        if successful_turn_streak >= EXPERIMENTAL_ONE_SHOT_SUCCESS_STREAK_TARGET:
            return ExperimentalOneShotRunResult(
                ending_status="success",
                ending_reason="completed_demo_arc",
                max_turns=max_turns,
                turn_records=turn_records,
                kp_result=kp_result,
                investigator_result=investigator_result,
                keeper_draft_result=keeper_draft_result,
                visible_draft_result=visible_draft_result,
                current_turn_index=current_turn_index,
                narrative_work_note_value=narrative_work_note_value,
                keeper_turn_note_value=keeper_turn_note_value,
                visible_turn_note_value=visible_turn_note_value,
                kp_turn_bridge=turn["kp_turn_bridge"],
                investigator_turn_bridge=turn["investigator_turn_bridge"],
                keeper_draft_applied=bool(turn["keeper_draft_applied"]),
                visible_draft_applied=bool(turn["visible_draft_applied"]),
            )
        if stagnation_streak >= EXPERIMENTAL_ONE_SHOT_STAGNATION_STREAK_LIMIT:
            return ExperimentalOneShotRunResult(
                ending_status="failure",
                ending_reason="stagnation_threshold",
                max_turns=max_turns,
                turn_records=turn_records,
                kp_result=kp_result,
                investigator_result=investigator_result,
                keeper_draft_result=keeper_draft_result,
                visible_draft_result=visible_draft_result,
                current_turn_index=current_turn_index,
                narrative_work_note_value=narrative_work_note_value,
                keeper_turn_note_value=keeper_turn_note_value,
                visible_turn_note_value=visible_turn_note_value,
                kp_turn_bridge=turn["kp_turn_bridge"],
                investigator_turn_bridge=turn["investigator_turn_bridge"],
                keeper_draft_applied=bool(turn["keeper_draft_applied"]),
                visible_draft_applied=bool(turn["visible_draft_applied"]),
            )
        if missing_continuity_streak >= EXPERIMENTAL_ONE_SHOT_MISSING_CONTINUITY_STREAK_LIMIT:
            return ExperimentalOneShotRunResult(
                ending_status="failure",
                ending_reason="missing_continuity_threshold",
                max_turns=max_turns,
                turn_records=turn_records,
                kp_result=kp_result,
                investigator_result=investigator_result,
                keeper_draft_result=keeper_draft_result,
                visible_draft_result=visible_draft_result,
                current_turn_index=current_turn_index,
                narrative_work_note_value=narrative_work_note_value,
                keeper_turn_note_value=keeper_turn_note_value,
                visible_turn_note_value=visible_turn_note_value,
                kp_turn_bridge=turn["kp_turn_bridge"],
                investigator_turn_bridge=turn["investigator_turn_bridge"],
                keeper_draft_applied=bool(turn["keeper_draft_applied"]),
                visible_draft_applied=bool(turn["visible_draft_applied"]),
            )

    return ExperimentalOneShotRunResult(
        ending_status="max_turns",
        ending_reason="turn_limit_reached",
        max_turns=max_turns,
        turn_records=turn_records,
        kp_result=last_turn["kp_result"],
        investigator_result=last_turn["investigator_result"],
        keeper_draft_result=last_turn["keeper_draft_result"],
        visible_draft_result=last_turn["visible_draft_result"],
        current_turn_index=current_turn_index,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_turn_note_value,
        visible_turn_note_value=visible_turn_note_value,
        kp_turn_bridge=last_turn["kp_turn_bridge"],
        investigator_turn_bridge=last_turn["investigator_turn_bridge"],
        keeper_draft_applied=bool(last_turn["keeper_draft_applied"]),
        visible_draft_applied=bool(last_turn["visible_draft_applied"]),
    )


def _build_experimental_one_shot_transcript_text(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> str:
    transcript_parts: list[str] = []
    for record in run_result.turn_records:
        transcript_parts.extend(
            [
                _normalize_form_text(record.kp_summary),
                _normalize_form_text(record.investigator_summary),
                _normalize_form_text(record.keeper_continuity),
                _normalize_form_text(record.visible_continuity),
                _normalize_form_text(record.narrative_work_note),
            ]
        )
    transcript_parts.extend(
        [
            _normalize_form_text(run_result.narrative_work_note_value),
            _normalize_form_text(run_result.keeper_turn_note_value),
            _normalize_form_text(run_result.visible_turn_note_value),
        ]
    )
    return "\n".join(part for part in transcript_parts if part)


def _experimental_one_shot_contains_any_cue(
    text: str,
    *,
    cues: tuple[str, ...],
) -> bool:
    return any(cue in text for cue in cues if cue)


def _experimental_scenario_preset_label(preset_id: str) -> str:
    config = EXPERIMENTAL_ONE_SHOT_PRESET_ENDING_CONFIGS.get(preset_id)
    if config is not None:
        return config.label
    return preset_id


def _serialize_experimental_one_shot_scenario_preset_internal_diagnostic(
    *,
    snapshot: Mapping[str, Any],
) -> ExperimentalScenarioPresetInternalDiagnostic | None:
    scenario = snapshot.get("scenario") or {}
    scenario_id = _normalize_form_text(scenario.get("scenario_id"))
    config = EXPERIMENTAL_ONE_SHOT_PRESET_ENDING_CONFIGS.get(scenario_id)
    if config is None:
        return None
    explanatory_text = _normalize_form_text(config.keeper_only_explanatory_text)
    if not explanatory_text:
        return None
    return {
        "preset_id": config.preset_id,
        "preset_label": config.label,
        "keeper_only_explanatory_text": explanatory_text,
    }


def _serialize_experimental_one_shot_scenario_preset_internal_diagnostic_json(
    diagnostic: ExperimentalScenarioPresetInternalDiagnostic | None,
) -> str:
    if diagnostic is None:
        return ""
    payload = {
        "preset_id": diagnostic["preset_id"],
        "preset_label": diagnostic["preset_label"],
        "keeper_only_explanatory_text": diagnostic["keeper_only_explanatory_text"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_experimental_one_shot_scenario_preset_internal_diagnostic_json(
    raw_value: Any,
) -> ExperimentalScenarioPresetInternalDiagnostic | None:
    normalized = _normalize_form_text(raw_value)
    if not normalized:
        return None
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    expected_keys = {
        "preset_id",
        "preset_label",
        "keeper_only_explanatory_text",
    }
    if set(payload) != expected_keys:
        return None
    preset_id = _normalize_form_text(payload.get("preset_id"))
    preset_label = _normalize_form_text(payload.get("preset_label"))
    explanatory_text = _normalize_form_text(payload.get("keeper_only_explanatory_text"))
    if not preset_id or not preset_label or not explanatory_text:
        return None
    return {
        "preset_id": preset_id,
        "preset_label": preset_label,
        "keeper_only_explanatory_text": explanatory_text,
    }


def _roundtrip_experimental_one_shot_scenario_preset_internal_diagnostic(
    diagnostic: ExperimentalScenarioPresetInternalDiagnostic | None,
) -> ExperimentalScenarioPresetInternalDiagnostic | None:
    return _parse_experimental_one_shot_scenario_preset_internal_diagnostic_json(
        _serialize_experimental_one_shot_scenario_preset_internal_diagnostic_json(
            diagnostic
        )
    )


def _read_experimental_one_shot_run_result_internal_diagnostic_snapshot(
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalScenarioPresetInternalDiagnostic | None:
    parsed = _parse_experimental_one_shot_scenario_preset_internal_diagnostic_json(
        run_result.scenario_preset_internal_diagnostic_json
    )
    return _roundtrip_experimental_one_shot_scenario_preset_internal_diagnostic(
        parsed
    )


def _read_experimental_one_shot_internal_diagnostic_for_internal_helper(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalScenarioPresetInternalDiagnostic | None:
    return _read_experimental_one_shot_run_result_internal_diagnostic_snapshot(
        run_result
    )


def _build_experimental_one_shot_internal_autopilot_seed_context(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotSeedContext | None:
    internal_diagnostic = _read_experimental_one_shot_internal_diagnostic_for_internal_helper(
        run_result=run_result,
    )
    if internal_diagnostic is None:
        return None
    return {
        "ending_status": run_result.ending_status,
        "preset_id": internal_diagnostic["preset_id"],
        "preset_label": internal_diagnostic["preset_label"],
        "keeper_only_explanatory_text": internal_diagnostic[
            "keeper_only_explanatory_text"
        ],
    }


def _build_experimental_one_shot_internal_autopilot_follow_up_hint(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotFollowUpHint | None:
    seed_context = _build_experimental_one_shot_internal_autopilot_seed_context(
        run_result=run_result,
    )
    if seed_context is None:
        return None
    follow_up_kind = (
        "preserve_anchor"
        if seed_context["ending_status"] == "success"
        else (
            "continue_anchor"
            if seed_context["ending_status"] == "max_turns"
            else "stabilize_anchor"
        )
    )
    return {
        "follow_up_kind": follow_up_kind,
        "preset_id": seed_context["preset_id"],
        "preset_label": seed_context["preset_label"],
        "keeper_anchor_text": seed_context["keeper_only_explanatory_text"],
    }


def _build_experimental_one_shot_internal_autopilot_next_step_recommendation(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotNextStepRecommendation | None:
    follow_up_hint = _build_experimental_one_shot_internal_autopilot_follow_up_hint(
        run_result=run_result,
    )
    if follow_up_hint is None:
        return None
    recommendation_kind = (
        "hold_anchor"
        if follow_up_hint["follow_up_kind"] == "preserve_anchor"
        else (
            "push_anchor"
            if follow_up_hint["follow_up_kind"] == "continue_anchor"
            else "recover_anchor"
        )
    )
    recommended_focus_text = (
        "优先保持当前 keeper 锚点："
        if recommendation_kind == "hold_anchor"
        else (
            "优先沿当前 keeper 锚点继续推进："
            if recommendation_kind == "push_anchor"
            else "优先先回到 keeper 锚点并稳定推进："
        )
    ) + follow_up_hint["keeper_anchor_text"]
    return {
        "recommendation_kind": recommendation_kind,
        "preset_id": follow_up_hint["preset_id"],
        "preset_label": follow_up_hint["preset_label"],
        "recommended_focus_text": recommended_focus_text,
    }


def _build_experimental_one_shot_internal_autopilot_micro_action(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotMicroAction | None:
    recommendation = _build_experimental_one_shot_internal_autopilot_next_step_recommendation(
        run_result=run_result,
    )
    if recommendation is None:
        return None
    action_kind = (
        "pin_focus"
        if recommendation["recommendation_kind"] == "hold_anchor"
        else (
            "advance_focus"
            if recommendation["recommendation_kind"] == "push_anchor"
            else "stabilize_focus"
        )
    )
    action_text = (
        "先做一条 keeper-only 聚焦动作："
        if action_kind == "pin_focus"
        else (
            "先做一条 keeper-only 微推进："
            if action_kind == "advance_focus"
            else "先做一条 keeper-only 稳定动作："
        )
    ) + recommendation["recommended_focus_text"]
    return {
        "action_kind": action_kind,
        "preset_id": recommendation["preset_id"],
        "preset_label": recommendation["preset_label"],
        "action_text": action_text,
    }


def _build_experimental_one_shot_internal_autopilot_execution_intent(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotExecutionIntent | None:
    micro_action = _build_experimental_one_shot_internal_autopilot_micro_action(
        run_result=run_result,
    )
    if micro_action is None:
        return None
    intent_kind = (
        "execute_pin_focus"
        if micro_action["action_kind"] == "pin_focus"
        else (
            "execute_advance_focus"
            if micro_action["action_kind"] == "advance_focus"
            else "execute_stabilize_focus"
        )
    )
    intent_text = "按当前 keeper-only 微动作执行：" + micro_action["action_text"]
    return {
        "intent_kind": intent_kind,
        "preset_id": micro_action["preset_id"],
        "preset_label": micro_action["preset_label"],
        "intent_text": intent_text,
    }


def _build_experimental_one_shot_internal_autopilot_executable_step_payload(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotExecutableStepPayload | None:
    execution_intent = _build_experimental_one_shot_internal_autopilot_execution_intent(
        run_result=run_result,
    )
    if execution_intent is None:
        return None
    payload_kind = (
        "payload_pin_focus"
        if execution_intent["intent_kind"] == "execute_pin_focus"
        else (
            "payload_advance_focus"
            if execution_intent["intent_kind"] == "execute_advance_focus"
            else "payload_stabilize_focus"
        )
    )
    payload_text = "按当前 keeper-only 执行意图形成单步 payload：" + execution_intent[
        "intent_text"
    ]
    return {
        "payload_kind": payload_kind,
        "preset_id": execution_intent["preset_id"],
        "preset_label": execution_intent["preset_label"],
        "payload_text": payload_text,
    }


def _build_experimental_one_shot_internal_autopilot_agent_input_envelope(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotAgentInputEnvelope | None:
    executable_step_payload = _build_experimental_one_shot_internal_autopilot_executable_step_payload(
        run_result=run_result,
    )
    if executable_step_payload is None:
        return None
    envelope_kind = (
        "envelope_pin_focus"
        if executable_step_payload["payload_kind"] == "payload_pin_focus"
        else (
            "envelope_advance_focus"
            if executable_step_payload["payload_kind"] == "payload_advance_focus"
            else "envelope_stabilize_focus"
        )
    )
    envelope_text = "封装为 internal agent 单步输入：" + executable_step_payload[
        "payload_text"
    ]
    return {
        "envelope_kind": envelope_kind,
        "preset_id": executable_step_payload["preset_id"],
        "preset_label": executable_step_payload["preset_label"],
        "envelope_text": envelope_text,
    }


def _build_experimental_one_shot_internal_autopilot_agent_turn_input(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotAgentTurnInput | None:
    agent_input_envelope = _build_experimental_one_shot_internal_autopilot_agent_input_envelope(
        run_result=run_result,
    )
    if agent_input_envelope is None:
        return None
    turn_input_kind = (
        "turn_input_pin_focus"
        if agent_input_envelope["envelope_kind"] == "envelope_pin_focus"
        else (
            "turn_input_advance_focus"
            if agent_input_envelope["envelope_kind"] == "envelope_advance_focus"
            else "turn_input_stabilize_focus"
        )
    )
    turn_input_text = "整理为 internal agent 单轮输入：" + agent_input_envelope[
        "envelope_text"
    ]
    return {
        "turn_input_kind": turn_input_kind,
        "preset_id": agent_input_envelope["preset_id"],
        "preset_label": agent_input_envelope["preset_label"],
        "turn_input_text": turn_input_text,
    }


def _build_experimental_one_shot_internal_autopilot_agent_turn_brief(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotAgentTurnBrief | None:
    agent_turn_input = _build_experimental_one_shot_internal_autopilot_agent_turn_input(
        run_result=run_result,
    )
    if agent_turn_input is None:
        return None
    brief_kind = (
        "brief_pin_focus"
        if agent_turn_input["turn_input_kind"] == "turn_input_pin_focus"
        else (
            "brief_advance_focus"
            if agent_turn_input["turn_input_kind"] == "turn_input_advance_focus"
            else "brief_stabilize_focus"
        )
    )
    brief_text = "整理为 internal agent 单轮摘要：" + agent_turn_input[
        "turn_input_text"
    ]
    return {
        "brief_kind": brief_kind,
        "preset_id": agent_turn_input["preset_id"],
        "preset_label": agent_turn_input["preset_label"],
        "brief_text": brief_text,
    }


def _build_experimental_one_shot_internal_autopilot_agent_turn_note(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotAgentTurnNote | None:
    agent_turn_brief = _build_experimental_one_shot_internal_autopilot_agent_turn_brief(
        run_result=run_result,
    )
    if agent_turn_brief is None:
        return None
    note_kind = (
        "note_pin_focus"
        if agent_turn_brief["brief_kind"] == "brief_pin_focus"
        else (
            "note_advance_focus"
            if agent_turn_brief["brief_kind"] == "brief_advance_focus"
            else "note_stabilize_focus"
        )
    )
    note_text = "整理为 internal agent 单轮便签：" + agent_turn_brief["brief_text"]
    return {
        "note_kind": note_kind,
        "preset_id": agent_turn_brief["preset_id"],
        "preset_label": agent_turn_brief["preset_label"],
        "note_text": note_text,
    }


def _build_experimental_one_shot_internal_autopilot_agent_turn_memo(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotAgentTurnMemo | None:
    agent_turn_note = _build_experimental_one_shot_internal_autopilot_agent_turn_note(
        run_result=run_result,
    )
    if agent_turn_note is None:
        return None
    memo_kind = (
        "memo_pin_focus"
        if agent_turn_note["note_kind"] == "note_pin_focus"
        else (
            "memo_advance_focus"
            if agent_turn_note["note_kind"] == "note_advance_focus"
            else "memo_stabilize_focus"
        )
    )
    memo_text = "整理为 internal agent 单轮 memo：" + agent_turn_note["note_text"]
    return {
        "memo_kind": memo_kind,
        "preset_id": agent_turn_note["preset_id"],
        "preset_label": agent_turn_note["preset_label"],
        "memo_text": memo_text,
    }


def _build_experimental_one_shot_internal_autopilot_agent_memo_input(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotInternalAutopilotAgentMemoInput | None:
    agent_turn_memo = _build_experimental_one_shot_internal_autopilot_agent_turn_memo(
        run_result=run_result,
    )
    if agent_turn_memo is None:
        return None
    input_kind = (
        "input_pin_focus"
        if agent_turn_memo["memo_kind"] == "memo_pin_focus"
        else (
            "input_advance_focus"
            if agent_turn_memo["memo_kind"] == "memo_advance_focus"
            else "input_stabilize_focus"
        )
    )
    input_text = "封装为 internal agent memo 输入：" + agent_turn_memo["memo_text"]
    return {
        "input_kind": input_kind,
        "preset_id": agent_turn_memo["preset_id"],
        "preset_label": agent_turn_memo["preset_label"],
        "input_text": input_text,
    }


def _finalize_experimental_one_shot_run_result_internal_tooling(
    *,
    snapshot: Mapping[str, Any],
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalOneShotRunResult:
    run_result.scenario_preset_ending = _judge_experimental_one_shot_scenario_preset_ending(
        snapshot=snapshot,
        run_result=run_result,
    )
    diagnostic = _serialize_experimental_one_shot_scenario_preset_internal_diagnostic(
        snapshot=snapshot,
    )
    run_result.scenario_preset_internal_diagnostic_json = (
        _serialize_experimental_one_shot_scenario_preset_internal_diagnostic_json(
            diagnostic
        )
    )
    run_result.scenario_preset_internal_diagnostic = (
        _read_experimental_one_shot_run_result_internal_diagnostic_snapshot(run_result)
    )
    return run_result


def _judge_generic_experimental_one_shot_scenario_preset_ending(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalScenarioPresetEnding:
    if run_result.ending_status == "aborted":
        return ExperimentalScenarioPresetEnding(
            preset_id="generic.experimental_one_shot",
            judgment="aborted",
            reason="当前受控 demo run 被中止，只有 run-local transcript 可供回看，不能继续解释成场景结局。",
            recap="这次 demo 在形成稳定收尾前就已中止；当前结果只是一段 non-authoritative 的实验记录。",
        )
    if run_result.ending_status == "failure":
        judgment = (
            "stalled_or_inconclusive"
            if run_result.ending_reason == "stagnation_threshold"
            else "collapse_or_failure"
        )
        reason = (
            "当前 demo run 连续空转，没有形成新的可解释推进，因此按停滞 / 未决处理。"
            if judgment == "stalled_or_inconclusive"
            else "当前 demo run 没能形成稳定推进或 continuity bridge，因此按崩坏 / 失败处理。"
        )
        recap = (
            "这次 demo 没有跑出足够清晰的收尾，只留下当前页 transcript 作为非权威实验观察。"
        )
        return ExperimentalScenarioPresetEnding(
            preset_id="generic.experimental_one_shot",
            judgment=judgment,
            reason=reason,
            recap=recap,
        )
    if run_result.ending_status == "max_turns":
        return ExperimentalScenarioPresetEnding(
            preset_id="generic.experimental_one_shot",
            judgment="stalled_or_inconclusive",
            reason="当前 demo run 到达轮数上限，只能按未决收尾解释，不能把它视为已完成的场景结局。",
            recap="这次 demo 在达到最大轮数后收束；保留下来的 transcript 只说明 run 停在了哪里。",
        )
    return ExperimentalScenarioPresetEnding(
        preset_id="generic.experimental_one_shot",
        judgment="partial_success",
        reason="当前 demo run 形成了可读 mini-arc，但缺少更具体的 preset 结局依据，因此只按部分成功解释。",
        recap="这次 demo 跑出了基本完整的 mini-arc，但当前结果仍只是 non-authoritative 的 demo ending。",
    )


def _judge_configured_experimental_one_shot_preset_ending(
    *,
    config: ExperimentalScenarioPresetJudgeConfig,
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalScenarioPresetEnding:
    transcript_text = _build_experimental_one_shot_transcript_text(run_result=run_result)
    has_decisive_cue = _experimental_one_shot_contains_any_cue(
        transcript_text,
        cues=config.visible_safe_cues.decisive,
    )
    has_progress_cue = _experimental_one_shot_contains_any_cue(
        transcript_text,
        cues=config.visible_safe_cues.progress,
    )
    if run_result.ending_status == "aborted":
        outcome = (
            config.visible_safe_endings.aborted_secret_breach
            if run_result.ending_reason == "visible_secret_breach"
            else config.visible_safe_endings.aborted_default
        )
        return ExperimentalScenarioPresetEnding(
            preset_id=config.preset_id,
            judgment="aborted",
            reason=outcome.reason,
            recap=outcome.recap,
        )
    if run_result.ending_status == "failure":
        if run_result.ending_reason == "stagnation_threshold":
            return ExperimentalScenarioPresetEnding(
                preset_id=config.preset_id,
                judgment="stalled_or_inconclusive",
                reason=config.visible_safe_endings.failure_stagnation.reason,
                recap=config.visible_safe_endings.failure_stagnation.recap,
            )
        return ExperimentalScenarioPresetEnding(
            preset_id=config.preset_id,
            judgment="collapse_or_failure",
            reason=config.visible_safe_endings.failure_default.reason,
            recap=config.visible_safe_endings.failure_default.recap,
        )
    if run_result.ending_status == "max_turns":
        if has_decisive_cue or has_progress_cue:
            return ExperimentalScenarioPresetEnding(
                preset_id=config.preset_id,
                judgment="partial_success",
                reason=config.visible_safe_endings.max_turns_partial.reason,
                recap=config.visible_safe_endings.max_turns_partial.recap,
            )
        return ExperimentalScenarioPresetEnding(
            preset_id=config.preset_id,
            judgment="stalled_or_inconclusive",
            reason=config.visible_safe_endings.max_turns_stalled.reason,
            recap=config.visible_safe_endings.max_turns_stalled.recap,
        )
    if has_decisive_cue:
        return ExperimentalScenarioPresetEnding(
            preset_id=config.preset_id,
            judgment="decisive_success",
            reason=config.visible_safe_endings.success_decisive.reason,
            recap=config.visible_safe_endings.success_decisive.recap,
        )
    if has_progress_cue:
        return ExperimentalScenarioPresetEnding(
            preset_id=config.preset_id,
            judgment="partial_success",
            reason=config.visible_safe_endings.success_partial.reason,
            recap=config.visible_safe_endings.success_partial.recap,
        )
    return ExperimentalScenarioPresetEnding(
        preset_id=config.preset_id,
        judgment="stalled_or_inconclusive",
        reason=config.visible_safe_endings.success_stalled.reason,
        recap=config.visible_safe_endings.success_stalled.recap,
    )


def _judge_experimental_one_shot_scenario_preset_ending(
    *,
    snapshot: Mapping[str, Any],
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalScenarioPresetEnding:
    scenario = snapshot.get("scenario") or {}
    scenario_id = _normalize_form_text(scenario.get("scenario_id"))
    config = EXPERIMENTAL_ONE_SHOT_PRESET_ENDING_CONFIGS.get(scenario_id)
    if config is not None:
        return _judge_configured_experimental_one_shot_preset_ending(
            config=config,
            run_result=run_result,
        )
    return _judge_generic_experimental_one_shot_scenario_preset_ending(
        run_result=run_result,
    )


def _assistant_result_hidden_json(
    result: LocalLLMAssistantResult | None,
) -> str:
    if result is None:
        return ""
    return result.model_dump_json()


def _assistant_result_from_hidden_json(
    raw_value: Any,
) -> LocalLLMAssistantResult | None:
    normalized = _normalize_form_text(raw_value)
    if not normalized:
        return None
    try:
        return LocalLLMAssistantResult.model_validate_json(normalized)
    except ValidationError:
        return None


def _form_flag_enabled(raw_value: Any) -> bool:
    return _normalize_form_text(raw_value) in {"1", "true", "yes", "on"}


def _build_experimental_kp_echo_bridge_from_flags(
    *,
    has_keeper_continuity: bool,
    has_visible_continuity: bool,
) -> dict[str, Any] | None:
    if not has_keeper_continuity and not has_visible_continuity:
        return None
    bridge: dict[str, Any] = {}
    if has_keeper_continuity:
        bridge["keeper_adoption_and_outcome_note"] = "used"
    if has_visible_continuity:
        bridge["public_outcome_note"] = "used"
    return bridge


def _build_experimental_investigator_echo_bridge_from_flags(
    *,
    has_visible_continuity: bool,
) -> dict[str, Any] | None:
    if not has_visible_continuity:
        return None
    return {"public_outcome_note": "used"}


def _normalize_experimental_demo_rubric_state(
    form: Mapping[str, Any],
) -> dict[str, str]:
    state: dict[str, str] = {}
    evaluation_label = _normalize_form_text(form.get("evaluation_label")) or ""
    if evaluation_label:
        state["evaluation_label"] = evaluation_label
    for field_name in EXPERIMENTAL_DEMO_RUBRIC_FIELD_LABELS:
        value = _normalize_form_text(form.get(field_name)) or ""
        if value in EXPERIMENTAL_DEMO_RUBRIC_VALUE_LABELS:
            state[field_name] = value
    free_note = _normalize_form_text(form.get("evaluation_note")) or ""
    if free_note:
        state["evaluation_note"] = free_note
    return state


def _render_hidden_experimental_demo_evaluation_state_inputs(
    evaluation_state: Mapping[str, str] | None,
) -> str:
    if not evaluation_state:
        return ""
    parts: list[str] = []
    for field_name in (
        ["evaluation_label"]
        + list(EXPERIMENTAL_DEMO_RUBRIC_FIELD_LABELS.keys())
        + ["evaluation_note"]
    ):
        value = _normalize_form_text(evaluation_state.get(field_name)) or ""
        if value:
            parts.append(
                f'<input type="hidden" name="{escape(field_name)}" value="{escape(value)}">'
            )
    return "".join(parts)


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


def _build_keeper_compressed_context_payload(
    *,
    service: SessionService,
    context_pack: dict[str, Any],
) -> dict[str, Any]:
    return service.build_keeper_compressed_context_from_context_pack(
        KeeperContextPack.model_validate(context_pack)
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
      <section id="{KEEPER_CONTEXT_PACK_ANCHOR_ID}" class="surface">
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


def _render_keeper_compressed_context_block(
    *,
    compressed_context: dict[str, Any],
    title: str = "Compact Recap / 压缩工作摘要",
    summary: str = "比 Keeper Context Pack 更短、更稳的 keeper-side compressed context，优先给 narrative scaffolding、recap assistant 和 future AI demo 复用；不是 authoritative truth。",
) -> str:
    immediate_pressures = list(compressed_context.get("immediate_pressures") or [])
    next_focus = list(compressed_context.get("next_focus") or [])
    prompt_focus = list(compressed_context.get("active_prompt_briefs") or [])
    knowledge_direction = list(compressed_context.get("knowledge_direction") or [])
    disclaimer = (
        _normalize_form_text(compressed_context.get("disclaimer"))
        or "这是非权威的 keeper-side 压缩工作摘要。"
    )
    situation_summary = _normalize_form_text(compressed_context.get("situation_summary")) or "当前局势暂无额外压缩摘要。"
    combat_summary = _normalize_form_text(compressed_context.get("combat_summary"))
    narrative_work_summary = _normalize_form_text(compressed_context.get("narrative_work_summary"))
    current_scene = _normalize_form_text(compressed_context.get("current_scene")) or "未知场景"
    current_beat_title = _normalize_form_text(compressed_context.get("current_beat_title")) or "未命名节点"
    pressure_html = "".join(f"<li>{escape(str(line))}</li>" for line in immediate_pressures[:3])
    focus_html = "".join(f"<li>{escape(str(line))}</li>" for line in next_focus[:3])
    prompt_html = "".join(f"<li>{escape(str(line))}</li>" for line in prompt_focus[:2])
    knowledge_html = "".join(f"<li>{escape(str(line))}</li>" for line in knowledge_direction[:2])
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>{escape(title)}</h2>
            <p>{escape(summary)}</p>
          </div>
          <span class="tag">compressed</span>
        </div>
        <p class="helper">{escape(disclaimer)}</p>
        <ul class="meta-list">
          <li>当前场景：{escape(current_scene)}</li>
          <li>当前 beat：{escape(current_beat_title)}</li>
          <li>当前局势一句话：{escape(situation_summary)}</li>
        </ul>
        <div class="divider"></div>
        <p class="meta-line">当前压力 / 未解决事项</p>
        {f'<ul class="meta-list">{pressure_html}</ul>' if pressure_html else '<p class="empty">当前没有额外即时压力摘要。</p>'}
        <p class="meta-line">当前最该推进</p>
        {f'<ul class="meta-list">{focus_html}</ul>' if focus_html else '<p class="empty">当前没有额外 next focus。</p>'}
        <p class="meta-line">当前 prompts</p>
        {f'<ul class="meta-list">{prompt_html}</ul>' if prompt_html else '<p class="empty">当前没有额外 prompt 摘要。</p>'}
        {
            (
                f'<p class="meta-line">战斗 / 伤势摘要：{escape(combat_summary)}</p>'
            )
            if combat_summary
            else ""
        }
        {
            (
                f'<p class="meta-line">当前 narrative 工作摘要：{escape(narrative_work_summary)}</p>'
            )
            if narrative_work_summary
            else ""
        }
        <p class="meta-line">资料方向</p>
        {f'<ul class="meta-list">{knowledge_html}</ul>' if knowledge_html else '<p class="empty">当前没有额外 knowledge direction。</p>'}
      </section>
    """


def _render_context_pack_source_echo(
    *,
    result: LocalLLMAssistantResult | None,
    context_pack: dict[str, Any] | None,
    suggestion_label: str,
) -> str:
    if result is None or result.status != "success" or result.assistant is None or context_pack is None:
        return ""
    coverage_bits = ["局势摘要", "未解决事项", "当前压力 / 线索方向"]
    if _normalize_form_text(context_pack.get("narrative_work_note")):
        coverage_bits.append("当前 narrative_work_note")
    coverage_text = "、".join(coverage_bits[:4])
    return f"""
      <article class="assistant-source-echo">
        <div class="list-head">
          <h3>当前输入来源</h3>
          <span class="tag">context pack</span>
        </div>
        <ul class="meta-list">
          <li>本次 {escape(suggestion_label)}基于当前 Keeper Context Pack。</li>
          <li>摘要范围：{escape(coverage_text)}。</li>
          <li>说明：这是 keeper-side 工作摘要输入，不是已执行结果，也不是 authoritative truth。</li>
        </ul>
        <div class="toolbar">
          <a class="button-link ghost" href="#{KEEPER_CONTEXT_PACK_ANCHOR_ID}">查看当前 Keeper Context Pack</a>
        </div>
      </article>
    """


def _render_compressed_context_source_echo(
    *,
    result: LocalLLMAssistantResult | None,
    compressed_context: dict[str, Any] | None,
    suggestion_label: str,
) -> str:
    if (
        result is None
        or result.status != "success"
        or result.assistant is None
        or compressed_context is None
    ):
        return ""
    return f"""
      <article class="assistant-source-echo">
        <div class="list-head">
          <h3>当前压缩输入来源</h3>
          <span class="tag">compressed</span>
        </div>
        <ul class="meta-list">
          <li>本次 {escape(suggestion_label)}优先参考当前 Compressed Context。</li>
          <li>压缩范围：当前局势、当前压力 / 未解决事项、当前最该推进方向。</li>
          <li>说明：这是 keeper-side 工作压缩摘要输入，不是已执行结果，也不是 authoritative truth。</li>
        </ul>
      </article>
    """


def _render_experimental_source_echo_card(
    *,
    title: str,
    lines: list[str],
) -> str:
    line_html = "".join(f"<li>{escape(line)}</li>" for line in lines if line)
    return f"""
      <article class="assistant-source-echo">
        <div class="list-head">
          <h3>{escape(title)}</h3>
          <span class="tag">experimental</span>
        </div>
        <ul class="meta-list">{line_html}</ul>
      </article>
    """


def _render_experimental_demo_source_echo(
    *,
    result: LocalLLMAssistantResult | None,
    title: str,
    lines: list[str],
) -> str:
    if result is None or result.status != "success" or result.assistant is None:
        return ""
    return _render_experimental_source_echo_card(title=title, lines=lines)


def _render_experimental_kp_continuity_source_echo(
    *,
    result: LocalLLMAssistantResult | None,
    turn_bridge: dict[str, Any] | None,
) -> str:
    if turn_bridge is None:
        return ""
    lines = ["本轮已参考上一轮 continuity bridge。"]
    if _normalize_form_text(turn_bridge.get("keeper_adoption_and_outcome_note")):
        lines.append("已纳入 keeper-side continuity note。")
    if _normalize_form_text(turn_bridge.get("public_outcome_note")):
        lines.append("已纳入公开可见 continuity note。")
    lines.append("说明：这些 continuity 只在当前实验页临时生效，不是已写入状态。")
    return _render_experimental_demo_source_echo(
        result=result,
        title="本轮 continuity 来源",
        lines=lines,
    )


def _render_experimental_investigator_continuity_source_echo(
    *,
    result: LocalLLMAssistantResult | None,
    turn_bridge: dict[str, Any] | None,
) -> str:
    if turn_bridge is None:
        return ""
    lines = [
        "本轮已参考上一轮公开 continuity bridge。",
        "输入只含当前页公开可见 continuity 摘要，不含 keeper-side continuity。",
        "说明：这是当前页临时实验上下文，不是已写入状态。",
    ]
    return _render_experimental_demo_source_echo(
        result=result,
        title="本轮 continuity 来源",
        lines=lines,
    )


def _render_experimental_keeper_drafting_source_echo(
    *,
    draft_applied: bool,
    evaluation_state: Mapping[str, str] | None = None,
) -> str:
    if not draft_applied:
        return ""
    lines = [
        "本次 keeper continuity draft 已参考当前 Compressed Context。",
        "已纳入当前轮 AI KP 输出摘要与 AI investigator 输出摘要。",
    ]
    if _build_experimental_demo_evaluation_hint(evaluation_state):
        lines.append("已参考当前页实验标签 / 评估备注。")
    lines.append("说明：这些只是当前页临时起草输入，不是已写入状态。")
    return _render_experimental_source_echo_card(
        title="keeper draft 起草来源",
        lines=lines,
    )


def _render_experimental_visible_drafting_source_echo(
    *,
    draft_applied: bool,
) -> str:
    if not draft_applied:
        return ""
    lines = [
        "本次 visible continuity draft 已参考当前 investigator visible summary。",
        "已纳入 recent visible events 与当前轮 AI investigator 输出摘要。",
        "说明：这只是当前页公开可见起草输入，不是已写入状态。",
    ]
    return _render_experimental_source_echo_card(
        title="visible draft 起草来源",
        lines=lines,
    )


def _render_experimental_investigator_input_block(
    *,
    investigator_view: dict[str, Any] | None,
) -> str:
    if investigator_view is None:
        return """
          <section class="surface">
            <div class="surface-header">
              <div>
                <h2>AI Investigator 输入摘要</h2>
                <p>当前没有可用于实验 harness 的调查员视角。</p>
              </div>
              <span class="tag warn">visible-only</span>
            </div>
            <p class="empty">请选择一个可用调查员后再运行实验回合。</p>
          </section>
        """
    viewer = investigator_view.get("viewer") or {}
    session = investigator_view.get("session") or {}
    own_state = investigator_view.get("own_state") or {}
    clues = list(investigator_view.get("visible_clues") or [])
    events = list(investigator_view.get("recent_events") or [])
    combat = investigator_view.get("combat") or {}
    flags = " / ".join(str(item) for item in own_state.get("status_flags") or []) or "无额外状态"
    clue_html = "".join(
        f"<li>{escape(str(item.get('title') or '线索'))}：{escape(str(item.get('summary') or ''))}</li>"
        for item in clues[:2]
    )
    event_html = "".join(
        f"<li>{escape(str(item.get('event_type') or 'event'))}：{escape(str(item.get('text') or ''))}</li>"
        for item in events[:2]
    )
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>AI Investigator 输入摘要</h2>
            <p>只取该调查员可见场景、线索、事件、角色状态与战斗摘要，不含 keeper-only 信息。</p>
          </div>
          <span class="tag">visible-only</span>
        </div>
        <ul class="meta-list">
          <li>调查员：{escape(str(viewer.get('display_name') or viewer.get('actor_id') or '未知调查员'))}</li>
          <li>当前场景：{escape(str(session.get('current_scene') or '未知场景'))}</li>
          <li>角色状态：HP {escape(str(own_state.get('current_hit_points') or '—'))} / SAN {escape(str(own_state.get('current_sanity') or '—'))}</li>
          <li>当前 flags：{escape(flags)}</li>
          <li>战斗摘要：当前行动者 {escape(str(combat.get('current_actor_id') or '无'))} / 回合 {escape(str(combat.get('round_number') or '—'))}</li>
        </ul>
        <p class="meta-line">可见线索</p>
        {f'<ul class="meta-list">{clue_html}</ul>' if clue_html else '<p class="empty">当前没有可见线索摘要。</p>'}
        <p class="meta-line">最近可见事件</p>
        {f'<ul class="meta-list">{event_html}</ul>' if event_html else '<p class="empty">当前没有最近可见事件摘要。</p>'}
      </section>
    """


def _render_experimental_ai_demo_output_block(
    *,
    title: str,
    summary: str,
    result: LocalLLMAssistantResult | None,
    source_echo_html: str = "",
) -> str:
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>{escape(title)}</h2>
            <p>{escape(summary)}</p>
          </div>
          <span class="tag warn">experimental</span>
        </div>
        <p class="helper">这是 isolated experimental demo 输出，只用于观察 AI KP / AI investigator 的最小叙事 loop，不会自动写入主状态。</p>
        <div class="card-list">
          {_render_local_llm_assistant_output(result)}
          {source_echo_html}
        </div>
      </section>
    """


def _render_experimental_ai_demo_preview_chain(
    *,
    session_id: str,
    current_turn_index: int,
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    keeper_draft_result: LocalLLMAssistantResult | None,
    visible_draft_result: LocalLLMAssistantResult | None,
) -> str:
    if (
        kp_result is None
        and investigator_result is None
        and keeper_draft_result is None
        and visible_draft_result is None
    ):
        return ""

    def _step_card(
        *,
        step_index: int,
        title: str,
        result: LocalLLMAssistantResult | None,
        summary: str,
        source_label: str,
        handoff_html: str = "",
    ) -> str:
        assistant = result.assistant if result is not None else None
        step_summary = _normalize_form_text(assistant.summary) if assistant is not None else ""
        if result is None:
            status_label = "not-run"
        elif result.status == "success":
            status_label = "ready"
        else:
            status_label = result.status
        return f"""
          <article class="assistant-source-echo">
            <div class="list-head">
              <h3>Step {step_index} · {escape(title)}</h3>
              <span class="tag">{escape(status_label)}</span>
            </div>
            <p class="helper">{escape(summary)}</p>
            <p class="meta-line">来源：直接运行自 {escape(source_label)}。</p>
            <p class="helper">说明：这是当前页 orchestration preview step，不是已执行结果。</p>
            {
                f'<p>{escape(step_summary)}</p>'
                if step_summary
                else '<p class="empty">当前没有可显示的结果摘要。</p>'
            }
            {handoff_html}
          </article>
        """

    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>Self-play Orchestration Preview</h2>
            <p>本次一次点击串行预演 AI KP、AI investigator、keeper continuity draft 与 visible continuity draft；只是 preview chain，不会自动提交或进入下一轮。当前预演轮次：{escape(str(current_turn_index or "未开始"))}</p>
          </div>
          <span class="tag warn">preview chain</span>
        </div>
        <div class="card-list">
          {_step_card(
              step_index=1,
              title="AI KP preview",
              result=kp_result,
              summary="先产出当前 scene framing / pressure / next beat 候选建议。",
              source_label="experimental AI KP demo block",
              handoff_html=_render_experimental_preview_handoff(
                  session_id=session_id,
                  step_key="ai-kp-preview",
                  result=kp_result,
                  button_label="带入 narrative_work_note",
                  target_id=_experimental_ai_demo_narrative_work_note_target_id(
                      session_id
                  ),
                  helper_text="当前 handoff 目标：当前页 narrative_work_note。点击后只带入文本，不会自动提交，也不会写入 authoritative state。",
                  adopted_status_text="已带入 preview narrative handoff 草稿。来源：self-play orchestration preview / AI KP preview。当前仍需 Keeper 人工编辑；这只是当前页 working text，不会自动提交或写入 authoritative state。",
              ),
          )}
          {_step_card(
              step_index=2,
              title="AI investigator preview",
              result=investigator_result,
              summary="再产出 investigator visible-side 行动提案。",
              source_label="experimental AI investigator demo block",
          )}
          {_step_card(
              step_index=3,
              title="keeper continuity draft",
              result=keeper_draft_result,
              summary="随后起草 keeper-side continuity bridge，并回填 textarea 供人工审阅。",
              source_label="experimental keeper continuity drafting block",
              handoff_html=_render_experimental_preview_handoff(
                  session_id=session_id,
                  step_key="keeper-continuity-draft",
                  result=keeper_draft_result,
                  button_label="重新带入 keeper continuity textarea",
                  target_id=_experimental_ai_demo_keeper_continuity_target_id(
                      session_id
                  ),
                  helper_text="当前 handoff 目标：当前页 keeper continuity textarea。preview 完成后已回填该 textarea；如需用当前预演版本覆盖 working text，可重新带入。不会自动提交，也不会写入 authoritative state。",
                  adopted_status_text="已重新带入 keeper continuity handoff 草稿。来源：self-play orchestration preview / keeper continuity draft。当前仍需 Keeper 人工编辑；这只是当前页 working text，不会自动提交或写入 authoritative state。",
              ),
          )}
          {_step_card(
              step_index=4,
              title="visible continuity draft",
              result=visible_draft_result,
              summary="最后起草 visible continuity bridge，并回填 textarea，但不会自动提交。",
              source_label="experimental visible continuity drafting block",
              handoff_html=_render_experimental_preview_handoff(
                  session_id=session_id,
                  step_key="visible-continuity-draft",
                  result=visible_draft_result,
                  button_label="重新带入 visible continuity textarea",
                  target_id=_experimental_ai_demo_visible_continuity_target_id(
                      session_id
                  ),
                  helper_text="当前 handoff 目标：当前页 visible continuity textarea。preview 完成后已回填该 textarea；如需用当前预演版本覆盖 working text，可重新带入。不会自动提交，也不会写入 authoritative state。",
                  adopted_status_text="已重新带入 visible continuity handoff 草稿。来源：self-play orchestration preview / visible continuity draft。当前仍需 Keeper 人工编辑；这只是当前页 working text，不会自动提交或写入 authoritative state。",
              ),
          )}
        </div>
      </section>
    """


def _render_experimental_ai_demo_one_shot_control(
    *,
    session_id: str,
    options_html: str,
    selected_investigator_id: str,
    max_turns: int,
    autopilot_token_surface_html: str = "",
    last_run_recall_html: str = "",
    last_run_recall_hidden_inputs_html: str = "",
    demo_boot: bool = False,
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    include_divider: bool = True,
) -> str:
    if not options_html:
        return ""
    demo_boot_hint_html = (
        '<p class="helper">demo-ready：当前页已默认选好 sample investigator 与 bounded turn limit。点击一次“一键开始 bounded autopilot run”即可直接观察 observer autoplay。</p>'
        if demo_boot
        else ""
    )
    divider_html = '<div class="divider"></div>' if include_divider else ""
    running_token_surface = _build_experimental_ai_demo_autopilot_token_surface(
        phase="running"
    )
    return f"""
      {divider_html}
      {autopilot_token_surface_html}
      {last_run_recall_html}
      <form id="experimental-demo-one-shot-control" method="post" action="/app/sessions/{escape(session_id)}/experimental-ai-demo/one-shot-run" class="form-stack" data-running-status-form="bounded-autopilot" data-running-status-surface="experimental-demo-autopilot-token-surface" data-running-status-badge="experimental-demo-autopilot-status-badge" data-running-status-text="experimental-demo-autopilot-status-text" data-running-status-detail="experimental-demo-autopilot-status-detail" data-running-cancel-like="experimental-demo-autopilot-cancel-like" data-running-status-label="{escape(running_token_surface.badge_label)}" data-running-status-tone="{escape(running_token_surface.badge_tone)}" data-running-status-text-value="{escape(running_token_surface.status_text)}" data-running-status-detail-value="{escape(running_token_surface.detail_text)}" data-running-cancel-like-value="{escape(running_token_surface.cancel_like_text)}" data-running-submit-text="running：正在等待当前响应">
        <label>
          Investigator 视角
          <select name="investigator_id">{options_html}</select>
        </label>
        <label>
          最大轮数
          <input type="number" name="max_turns" min="1" max="{EXPERIMENTAL_ONE_SHOT_MAX_TURNS_LIMIT}" value="{escape(str(max_turns))}">
        </label>
        {'<input type="hidden" name="demo_boot" value="1">' if demo_boot else ''}
        <input type="hidden" name="seed_narrative_work_note" value="{escape(narrative_work_note_value)}">
        <input type="hidden" name="seed_keeper_turn_outcome_note" value="{escape(keeper_turn_note_value)}">
        <input type="hidden" name="seed_visible_turn_outcome_note" value="{escape(visible_turn_note_value)}">
        {last_run_recall_hidden_inputs_html}
        {_render_hidden_experimental_demo_evaluation_state_inputs(evaluation_state)}
        {demo_boot_hint_html}
        <p class="helper">受控 bounded autopilot run：每轮顺序复用 AI KP、AI investigator、keeper continuity draft 与 visible continuity draft，直到成功 / 失败 / 中止 / 达到轮数上限；只保留当前页 run-local transcript，不会写入 authoritative state。</p>
        <div class="toolbar">
          <button class="button-button" type="submit">一键开始 bounded autopilot run</button>
        </div>
      </form>
    """


def _render_experimental_demo_collapsible_block(
    *,
    section_id: str,
    title: str,
    badge: str,
    body_html: str,
    helper_text: str = "",
    open_by_default: bool = False,
) -> str:
    if not body_html.strip():
        return ""
    open_attr = " open" if open_by_default else ""
    helper_html = f'<p class="helper">{escape(helper_text)}</p>' if helper_text else ""
    return f"""
      <details id="{escape(section_id)}" class="assistant-source-echo experimental-collapsible-block"{open_attr}>
        <summary class="list-head">
          <h3>{escape(title)}</h3>
          <span class="tag">{escape(badge)}</span>
        </summary>
        <div class="collapsible-body">
          {helper_html}
          <div class="card-list">{body_html}</div>
        </div>
      </details>
    """


def _render_experimental_demo_collapsible_surface(
    *,
    section_id: str,
    title: str,
    summary: str,
    body_html: str,
    badge: str = "secondary",
    helper_text: str = "",
    open_by_default: bool = False,
) -> str:
    if not body_html.strip():
        return ""
    open_attr = " open" if open_by_default else ""
    helper_html = f'<p class="helper">{escape(helper_text)}</p>' if helper_text else ""
    return f"""
      <details id="{escape(section_id)}" class="surface collapsible-surface"{open_attr}>
        <summary class="collapsible-summary">
          <div>
            <h2>{escape(title)}</h2>
            <p>{escape(summary)}</p>
          </div>
          <span class="tag">{escape(badge)}</span>
        </summary>
        <div class="collapsible-body">
          {helper_html}
          {body_html}
        </div>
      </details>
    """


def _render_experimental_ai_demo_primary_controls(
    *,
    session_id: str,
    options_html: str,
    initial_run_button_label: str,
    one_shot_max_turns: int,
    autopilot_token_surface_html: str = "",
    last_run_recall_html: str = "",
    last_run_recall_hidden_inputs_html: str = "",
    demo_boot: bool = False,
    selected_investigator_id: str = "",
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
) -> str:
    if not options_html:
        return """
          <article class="assistant-source-echo">
            <div class="list-head">
              <h3>主控制入口</h3>
              <span class="tag warn">missing investigator</span>
            </div>
            <p class="empty">当前没有可用于实验 harness 的 investigator 视角。</p>
          </article>
        """
    return f"""
      <article class="assistant-source-echo">
        <div class="list-head">
          <h3>一键 Bounded Autopilot</h3>
          <span class="tag warn">one-click CTA</span>
        </div>
        {_render_experimental_ai_demo_one_shot_control(
            session_id=session_id,
            options_html=options_html,
            selected_investigator_id=selected_investigator_id,
            max_turns=one_shot_max_turns,
            autopilot_token_surface_html=autopilot_token_surface_html,
            last_run_recall_html=last_run_recall_html,
            last_run_recall_hidden_inputs_html=last_run_recall_hidden_inputs_html,
            demo_boot=demo_boot,
            evaluation_state=evaluation_state,
            narrative_work_note_value=narrative_work_note_value,
            keeper_turn_note_value=keeper_turn_note_value,
            visible_turn_note_value=visible_turn_note_value,
            include_divider=False,
        )}
      </article>
      <article class="assistant-source-echo">
        <div class="list-head">
          <h3>单轮 / 预演入口</h3>
          <span class="tag">secondary path</span>
        </div>
        <form method="post" action="/app/sessions/{escape(session_id)}/experimental-ai-demo/run" class="form-stack">
          {('<input type="hidden" name="demo_boot" value="1" />' if demo_boot else '')}
          <label>
            Investigator 视角
            <select name="investigator_id">{options_html}</select>
          </label>
          <div class="toolbar">
            <button class="button-button secondary" type="submit">{escape(initial_run_button_label)}</button>
            <button class="button-button ghost" type="submit" formaction="/app/sessions/{escape(session_id)}/experimental-ai-demo/self-play-preview">运行 self-play 预演链</button>
          </div>
        </form>
      </article>
    """


def _render_experimental_ai_demo_workspace_strip(
    *,
    session_id: str,
    investigator_candidates: list[dict[str, Any]],
    selected_investigator_id: str | None,
    current_turn_index: int,
    controls_html: str,
    next_last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
    demo_boot: bool = False,
    one_shot_run_visible: bool = False,
) -> str:
    selected_label = "未选择"
    for item in investigator_candidates:
        actor_id = str(item.get("actor_id") or "")
        if actor_id == (selected_investigator_id or ""):
            selected_label = str(item.get("display_name") or actor_id or "调查员")
            break
    workspace_mode = "等待 Keeper 触发"
    if current_turn_index > 0:
        workspace_mode = f"第 {current_turn_index} 轮页内结果已就绪"
    if one_shot_run_visible:
        workspace_mode = "bounded autoplay run 已完成并可回看"
    entry_label = "Launcher Demo Boot" if demo_boot else "直接实验页"
    stage_label = "实验工作台待启动"
    next_action_label = "点击一次一键 bounded autopilot run，或先切到单轮实验 / self-play 预演。"
    workflow_step_tones = {
        "launcher": "success" if demo_boot else "",
        "setup": "warn" if demo_boot else "",
        "run": "warn" if not demo_boot else "",
        "observe": "",
        "rerun": "",
    }
    if demo_boot:
        stage_label = "一键 bounded autopilot 就绪"
        next_action_label = "当前页已默认选好 investigator 与 bounded turn limit；点击一次一键 bounded autopilot run 即可。"
    if current_turn_index > 0:
        stage_label = f"第 {current_turn_index} 轮结果已就绪"
        next_action_label = "先审阅当前轮 observer / 输出结果，再决定 continuity、rerun、fresh 或再次一键 bounded autopilot run。"
        workflow_step_tones = {
            "launcher": "success" if demo_boot else "",
            "setup": "success",
            "run": "success",
            "observe": "warn",
            "rerun": "",
        }
    if one_shot_run_visible:
        stage_label = "Observer 回看阶段"
        next_action_label = "先回看 bounded autopilot 结果，再决定 rerun 当前 session、fresh 重开或再次一键自动跑。"
        workflow_step_tones = {
            "launcher": "success" if demo_boot else "",
            "setup": "success",
            "run": "success",
            "observe": "warn",
            "rerun": "",
        }
    workflow_tags_html = "".join(
        f'<span class="tag{(" " + tone) if tone else ""}">{label}</span>'
        for label, tone in (
            ("Launcher 入口壳", workflow_step_tones["launcher"]),
            ("Setup / Reuse", workflow_step_tones["setup"]),
            ("Run / Preview", workflow_step_tones["run"]),
            ("Observer / 回看", workflow_step_tones["observe"]),
            ("Rerun / Fresh", workflow_step_tones["rerun"]),
        )
    )
    demo_setup_href = _experimental_ai_demo_setup_boot_href_with_recall(
        recall=next_last_run_recall
    )
    recent_demo_href = _append_query_params(
        "/app/experimental-ai-demo?demo_boot=1",
        _experimental_autopilot_last_run_recall_query_params(next_last_run_recall),
    )
    fresh_demo_href = _experimental_ai_demo_setup_boot_href_with_recall(
        fresh=True,
        recall=next_last_run_recall,
    )
    return f"""
      <section id="experimental-demo-workspace-strip" class="surface experimental-workspace-strip">
        <div class="surface-header">
          <div>
            <p class="eyebrow">Local Web App Shell</p>
            <h2>Demo Control Surface</h2>
            <p>Launcher 小窗只负责打开当前本地 Web 工作台；setup、run、observe、rerun 与 fresh 的主操作都收在这页完成。</p>
          </div>
          <span class="tag warn">workflow-first MVP</span>
        </div>
        <article id="experimental-demo-shell-identity" class="assistant-source-echo">
          <div class="list-head">
            <h3>本地 Web App 壳身份</h3>
            <span class="tag">shell identity</span>
          </div>
          <p>当前页是 keeper/internal demo 的主表面；launcher 只是 very small opener，不承担更多 workflow。</p>
          <div class="metric-grid">
            <div class="metric">
              <p class="metric-label">当前 Demo</p>
              <strong>session {escape(session_id)}</strong>
              <span>{escape(entry_label)}</span>
            </div>
            <div class="metric">
              <p class="metric-label">当前阶段</p>
              <strong>{escape(stage_label)}</strong>
              <span>{escape(workspace_mode)}</span>
            </div>
            <div class="metric">
              <p class="metric-label">当前视角</p>
              <strong>{escape(selected_label)}</strong>
              <span>keeper / internal only</span>
            </div>
            <div class="metric">
              <p class="metric-label">下一步</p>
              <strong>{escape(next_action_label)}</strong>
              <span>setup -> run -> observe -> rerun / fresh</span>
            </div>
          </div>
          <div id="experimental-demo-workflow-strip" class="pill-row">
            {workflow_tags_html}
          </div>
          <p class="helper"><strong>Workflow-first</strong>：setup 用于建新 demo 或 fresh 重开；单轮、预演和 bounded autoplay 在当前页运行，observer 回看也留在当前页完成。</p>
          <div class="toolbar">
            <a class="button-link secondary" href="{escape(demo_setup_href)}">Demo Setup</a>
            <a class="button-link ghost" href="/app/sessions/{escape(session_id)}">当前 Session 总览</a>
            <a class="button-link ghost" href="{escape(recent_demo_href)}">续看最近 Demo</a>
            <a class="button-link ghost" href="{escape(fresh_demo_href)}">启动全新 Demo</a>
          </div>
        </article>
        <div class="surface-header experimental-workspace-section-head">
          <div>
            <h3>Single-screen KP Workspace</h3>
            <p>首屏优先保留 autoplay 控制、observer 状态与最近结果；低频输入、输出、预演与评估细节收进下方折叠区。</p>
          </div>
          <span class="tag warn">single-screen MVP</span>
        </div>
        <p class="helper"><strong>Experimental / Non-authoritative</strong>：这仍是 keeper/internal experimental workspace，不是 full autopilot runtime，也不是最终消费者 app shell。</p>
        <div class="experimental-workspace-grid">
          <div class="card-list">
            {controls_html}
          </div>
          <div class="card-list">
            <article class="assistant-source-echo">
              <div class="list-head">
                <h3>首屏工作摘要</h3>
                <span class="tag">workspace focus</span>
              </div>
              <div class="metric-grid">
                <div class="metric">
                  <p class="metric-label">当前视角</p>
                  <strong>{escape(selected_label)}</strong>
                  <span>{escape(entry_label)}</span>
                </div>
                <div class="metric">
                  <p class="metric-label">当前实验轮次</p>
                  <strong>{escape(str(current_turn_index or "未开始"))}</strong>
                  <span>session {escape(session_id)}</span>
                </div>
                <div class="metric">
                  <p class="metric-label">当前主模式</p>
                  <strong>{escape(workspace_mode)}</strong>
                  <span>keeper / internal only</span>
                </div>
              </div>
            </article>
          </div>
        </div>
      </section>
    """


def _build_experimental_ai_demo_autopilot_token_surface(
    *,
    phase: str | None = None,
    run_result: ExperimentalOneShotRunResult | None = None,
) -> ExperimentalAutopilotTokenSurface:
    active_cancel_like_text = (
        "当前请求不支持 mid-run cancel；如果已经发出，只能等待响应完成后再决定 rerun 或启动全新 Demo。"
    )
    if phase == "running":
        current_copy = _build_experimental_autopilot_runtime_copy(
            subject_label="当前请求",
            status_label="运行中",
        )
        return ExperimentalAutopilotTokenSurface(
            phase="running",
            badge_label="running",
            badge_tone="warn",
            status_text=current_copy.status_text,
            detail_text=(
                "当前请求仍是 request-bounded 页面请求；observer / per-turn "
                "snapshots / finalized snapshots 会在响应完成后一起回填，不是后台 job 系统。"
            ),
            cancel_like_text=active_cancel_like_text,
        )
    if run_result is None:
        current_copy = _build_experimental_autopilot_runtime_copy(
            subject_label="当前请求",
            status_label="尚未开始",
        )
        return ExperimentalAutopilotTokenSurface(
            phase="ready",
            badge_label="ready",
            badge_tone="",
            status_text=current_copy.status_text,
            detail_text=(
                "点击主按钮后，当前页会先显示 running token；observer / per-turn "
                "snapshots / finalized snapshots 会在响应完成后回填。"
                "当前仍是 request-bounded 页面请求，不是后台 job 系统。"
            ),
            cancel_like_text=active_cancel_like_text,
        )
    status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        run_result.ending_status,
        run_result.ending_status,
    )
    reason_label = EXPERIMENTAL_ONE_SHOT_ENDING_REASON_LABELS.get(
        run_result.ending_reason,
        run_result.ending_reason,
    )
    badge_tone = "success"
    if run_result.ending_status == "max_turns":
        badge_tone = "warn"
    elif run_result.ending_status in {"failure", "aborted"}:
        badge_tone = "danger"
    runtime_recall = _build_experimental_autopilot_last_run_recall_from_run_result(
        run_result
    )
    current_copy = _build_experimental_autopilot_runtime_copy(
        subject_label="当前请求",
        status_label=status_label,
        reason_label=reason_label,
        provider_name=runtime_recall.provider_name,
        model=runtime_recall.model,
    )
    return ExperimentalAutopilotTokenSurface(
        phase="done",
        badge_label="done",
        badge_tone=badge_tone,
        status_text=current_copy.status_text,
        detail_text=(
            "observer、per-turn snapshots 与 finalized snapshots 已按当前 run "
            "回填；当前结果仍然只是 run-local transcript，不会写入 authoritative state。"
        ),
        stop_reason_text=current_copy.stop_reason_text,
        runtime_text=current_copy.runtime_text,
        cancel_like_text=(
            "当前请求已完成；如果你想放弃这次结果，请直接点击“启动全新 Demo”重新开始。"
        ),
    )


def _render_experimental_ai_demo_autopilot_token_surface(
    *,
    token_surface: ExperimentalAutopilotTokenSurface,
) -> str:
    badge_class = "tag"
    if token_surface.badge_tone:
        badge_class += f" {token_surface.badge_tone}"
    stop_reason_html = ""
    if token_surface.stop_reason_text:
        stop_reason_html = (
            f'<li id="experimental-demo-autopilot-stop-reason">'
            f"{escape(token_surface.stop_reason_text)}</li>"
        )
    runtime_html = ""
    if token_surface.runtime_text:
        runtime_html = (
            f'<li id="experimental-demo-autopilot-runtime-text">'
            f"{escape(token_surface.runtime_text)}</li>"
        )
    return f"""
      <article id="experimental-demo-autopilot-token-surface" class="assistant-source-echo" data-autopilot-token-phase="{escape(token_surface.phase)}">
        <div class="list-head">
          <h3>Autopilot Request Token</h3>
          <span id="experimental-demo-autopilot-status-badge" class="{escape(badge_class)}">{escape(token_surface.badge_label)}</span>
        </div>
        <p id="experimental-demo-autopilot-status-text">{escape(token_surface.status_text)}</p>
        <ul class="meta-list">
          <li id="experimental-demo-autopilot-status-detail">{escape(token_surface.detail_text)}</li>
          {stop_reason_html}
          {runtime_html}
        </ul>
        <p class="helper"><span class="tag warn">cancel-like</span> <span id="experimental-demo-autopilot-cancel-like">{escape(token_surface.cancel_like_text)}</span></p>
      </article>
    """


def _build_experimental_one_shot_autoplay_observer_cards(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    seed_context = _build_experimental_one_shot_internal_autopilot_seed_context(
        run_result=run_result,
    )
    if seed_context is not None:
        cards.append(
            {
                "title": "种子上下文",
                "badge": EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
                    seed_context["ending_status"],
                    seed_context["ending_status"],
                ),
                "preset": f'{seed_context["preset_label"]}（{seed_context["preset_id"]}）',
                "summary": _excerpt(
                    seed_context["keeper_only_explanatory_text"],
                    limit=180,
                ),
            }
        )
    follow_up_hint = _build_experimental_one_shot_internal_autopilot_follow_up_hint(
        run_result=run_result,
    )
    if follow_up_hint is not None:
        cards.append(
            {
                "title": "跟进提示",
                "badge": follow_up_hint["follow_up_kind"],
                "preset": f'{follow_up_hint["preset_label"]}（{follow_up_hint["preset_id"]}）',
                "summary": _excerpt(
                    follow_up_hint["keeper_anchor_text"],
                    limit=180,
                ),
            }
        )
    execution_intent = _build_experimental_one_shot_internal_autopilot_execution_intent(
        run_result=run_result,
    )
    if execution_intent is not None:
        cards.append(
            {
                "title": "执行意图",
                "badge": execution_intent["intent_kind"],
                "preset": f'{execution_intent["preset_label"]}（{execution_intent["preset_id"]}）',
                "summary": _excerpt(
                    execution_intent["intent_text"],
                    limit=180,
                ),
            }
        )
    agent_memo_input = _build_experimental_one_shot_internal_autopilot_agent_memo_input(
        run_result=run_result,
    )
    if agent_memo_input is not None:
        cards.append(
            {
                "title": "Memo 输入",
                "badge": agent_memo_input["input_kind"],
                "preset": f'{agent_memo_input["preset_label"]}（{agent_memo_input["preset_id"]}）',
                "summary": _excerpt(
                    agent_memo_input["input_text"],
                    limit=180,
                ),
            }
        )
    return cards


def _build_experimental_one_shot_turn_finalized_internal_snapshot(
    *,
    record: ExperimentalOneShotTurnRecord,
) -> ExperimentalOneShotTurnFinalizedInternalSnapshot | None:
    keeper_continuity = _normalize_form_text(record.keeper_continuity)
    if keeper_continuity:
        return {
            "kind": "turn_memo",
            "text": "整理为当前轮 finalized memo：" + keeper_continuity,
        }
    narrative_work_note = _normalize_form_text(record.narrative_work_note)
    if narrative_work_note:
        return {
            "kind": "turn_note",
            "text": "整理为当前轮 finalized note：" + narrative_work_note,
        }
    signature = _normalize_form_text(record.signature)
    if signature:
        return {
            "kind": "run_local_signature",
            "text": "回落到当前轮 run-local signature：" + signature,
        }
    return None


def _build_experimental_one_shot_recent_turn_finalized_snapshot_contract(
    *,
    run_result: ExperimentalOneShotRunResult,
    limit: int | None = None,
) -> list[ExperimentalOneShotRecentTurnFinalizedSnapshotItem]:
    if not run_result.turn_records:
        return []
    selected_records = (
        run_result.turn_records
        if limit is None
        else (
            run_result.turn_records[-limit:]
            if limit > 0
            else []
        )
    )
    final_turn_index = run_result.turn_records[-1].turn_index
    final_status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        run_result.ending_status,
        run_result.ending_status,
    )
    final_stop_reason = EXPERIMENTAL_ONE_SHOT_ENDING_REASON_LABELS.get(
        run_result.ending_reason,
        run_result.ending_reason,
    )
    contract_items: list[ExperimentalOneShotRecentTurnFinalizedSnapshotItem] = []
    for record in selected_records:
        finalized_snapshot = _build_experimental_one_shot_turn_finalized_internal_snapshot(
            record=record,
        )
        is_final_turn = record.turn_index == final_turn_index
        contract_items.append(
            {
                "turn_index": record.turn_index,
                "status_label": final_status_label if is_final_turn else "已完成",
                "finalized_kind": (
                    finalized_snapshot["kind"]
                    if finalized_snapshot is not None
                    else "unavailable"
                ),
                "finalized_text": (
                    _excerpt(finalized_snapshot["text"], limit=180)
                    if finalized_snapshot is not None
                    else "当前轮未产出 representative finalized internal snapshot。"
                ),
                "stop_reason": final_stop_reason if is_final_turn else "",
            }
        )
    return contract_items


def _build_experimental_one_shot_autoplay_turn_observer_snapshots(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> list[dict[str, str]]:
    if not run_result.turn_records:
        return []
    recent_turn_contract = (
        _build_experimental_one_shot_recent_turn_finalized_snapshot_contract(
            run_result=run_result,
        )
    )
    contract_by_turn_index = {
        item["turn_index"]: item for item in recent_turn_contract
    }
    final_turn_index = run_result.turn_records[-1].turn_index
    snapshots: list[dict[str, str]] = []
    for record in run_result.turn_records:
        is_final_turn = record.turn_index == final_turn_index
        run_local_summary = _excerpt(
            record.signature
            or record.kp_summary
            or record.investigator_summary
            or record.narrative_work_note
            or "当前无可用 run-local 摘要。",
            limit=180,
        )
        continuity_summary = _excerpt(
            record.keeper_continuity
            or record.visible_continuity
            or record.narrative_work_note
            or "当前无 continuity 摘要。",
            limit=140,
        )
        finalized_contract = contract_by_turn_index.get(record.turn_index)
        snapshots.append(
            {
                "title": f"第 {record.turn_index} 轮快照",
                "badge": (
                    finalized_contract["status_label"]
                    if finalized_contract is not None
                    else ("已完成" if not is_final_turn else run_result.ending_status)
                ),
                "phase": "结束轮" if is_final_turn else "中间轮",
                "run_local_summary": run_local_summary,
                "continuity_summary": continuity_summary,
                "finalized_kind": (
                    finalized_contract["finalized_kind"]
                    if finalized_contract is not None
                    else "unavailable"
                ),
                "finalized_text": (
                    finalized_contract["finalized_text"]
                    if finalized_contract is not None
                    else "当前轮未产出 representative finalized internal snapshot。"
                ),
                "stop_reason": (
                    finalized_contract["stop_reason"]
                    if finalized_contract is not None
                    else ""
                ),
            }
        )
    return snapshots


def _render_experimental_one_shot_autoplay_observer_panel(
    *,
    run_result: ExperimentalOneShotRunResult | None = None,
    last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
) -> str:
    observer_last_run_recall_html = _render_experimental_observer_last_run_recall_row(
        last_run_recall
    )
    if run_result is None or not run_result.turn_records:
        return f"""
      <section id="experimental-demo-observer" class="surface">
        <div class="surface-header">
          <div>
            <h2>Autoplay Observer</h2>
            <p>观察型 keeper/internal 面板：点击一次 bounded autopilot run 后，会在这里显示 bounded autoplay 状态、逐轮 transcript 与 very small internal helper chain 快照。</p>
          </div>
          <span class="tag">idle</span>
        </div>
        {observer_last_run_recall_html}
        <ul class="meta-list">
          <li>模式：bounded one-shot autoplay</li>
          <li>当前状态：尚未运行。</li>
          <li>停止边界：success / failure / aborted / max_turns。</li>
          <li>按轮快照：运行后会在这里追加 very small run-local snapshot 列表。</li>
          <li>按轮 finalized：运行后会在每轮卡片里附加 1 个代表性的 finalized internal object。</li>
          <li>说明：这里只显示 keeper/internal observer 内容，不是公开 explainability contract，也不会写入 authoritative state。</li>
        </ul>
      </section>
    """
    status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        run_result.ending_status,
        run_result.ending_status,
    )
    reason_label = EXPERIMENTAL_ONE_SHOT_ENDING_REASON_LABELS.get(
        run_result.ending_reason,
        run_result.ending_reason,
    )
    chain_cards = _build_experimental_one_shot_autoplay_observer_cards(
        run_result=run_result,
    )
    turn_snapshots = _build_experimental_one_shot_autoplay_turn_observer_snapshots(
        run_result=run_result,
    )
    chain_cards_html = "".join(
        f"""
        <article class="assistant-source-echo">
          <div class="list-head">
            <h3>{escape(card["title"])}</h3>
            <span class="tag">{escape(card["badge"])}</span>
          </div>
          <ul class="meta-list">
            <li>preset：{escape(card["preset"])}</li>
            <li>observer 摘要：{escape(card["summary"])}</li>
          </ul>
        </article>
        """
        for card in chain_cards
    )
    turn_snapshot_html = "".join(
        f"""
        <article class="assistant-source-echo">
          <div class="list-head">
            <h3>{escape(snapshot["title"])}</h3>
            <span class="tag">{escape(snapshot["badge"])}</span>
          </div>
          <ul class="meta-list">
            <li>轮次阶段：{escape(snapshot["phase"])}</li>
            <li>run-local 摘要：{escape(snapshot["run_local_summary"])}</li>
            <li>continuity 摘要：{escape(snapshot["continuity_summary"])}</li>
            <li>finalized object：{escape(snapshot["finalized_kind"])}</li>
            <li>finalized 摘要：{escape(snapshot["finalized_text"])}</li>
            {f'<li>停止原因：{escape(snapshot["stop_reason"])}</li>' if snapshot["stop_reason"] else ''}
          </ul>
        </article>
        """
        for snapshot in turn_snapshots
    )
    latest_snapshot = turn_snapshots[-1]
    latest_snapshot_html = f"""
      <article class="assistant-source-echo">
        <div class="list-head">
          <h3>最近一轮内部快照</h3>
          <span class="tag warn">{escape(latest_snapshot["badge"])}</span>
        </div>
        <ul class="meta-list">
          <li>轮次阶段：{escape(latest_snapshot["phase"])}</li>
          <li>run-local 摘要：{escape(latest_snapshot["run_local_summary"])}</li>
          <li>continuity 摘要：{escape(latest_snapshot["continuity_summary"])}</li>
          <li>finalized object：{escape(latest_snapshot["finalized_kind"])}</li>
          <li>finalized 摘要：{escape(latest_snapshot["finalized_text"])}</li>
          {f'<li>停止原因：{escape(latest_snapshot["stop_reason"])}</li>' if latest_snapshot["stop_reason"] else ''}
        </ul>
      </article>
    """
    return f"""
      <section id="experimental-demo-observer" class="surface">
        <div class="surface-header">
          <div>
            <h2>Autoplay Observer</h2>
            <p>观察当前 bounded autoplay run 的状态与 very small internal helper chain 快照。这里只是 keeper/internal observer，不是 full autopilot runtime，也不是公开 explainability 面板。</p>
          </div>
          <span class="tag warn">{escape(status_label)}</span>
        </div>
        {observer_last_run_recall_html}
        <ul class="meta-list">
          <li>模式：bounded one-shot autoplay</li>
          <li>当前状态：{escape(status_label)}。</li>
          <li>当前停止原因：{escape(reason_label)}</li>
          <li>已跑轮次：{escape(str(len(run_result.turn_records)))} / 最大 {escape(str(run_result.max_turns))}</li>
          <li>当前只显示 4 个代表性 internal helper object：种子上下文、跟进提示、执行意图、Memo 输入。</li>
        </ul>
        {latest_snapshot_html}
        {_render_experimental_demo_collapsible_block(
            section_id="experimental-demo-observer-chain",
            title="internal helper chain 详情",
            badge="4 items",
            body_html=chain_cards_html,
            helper_text="这些是当前 run 的 very small internal helper chain 摘要，不是公开 explainability contract。",
        )}
        {_render_experimental_demo_collapsible_block(
            section_id="experimental-demo-observer-turn-history",
            title="按轮内部快照",
            badge="turn-by-turn",
            body_html=turn_snapshot_html,
            helper_text="当前 helper chain 仍是 run-result-level final snapshot；按轮 finalized 先收成 recent-turn contract，再由 observer 卡片复用。contract 只保留 turn_index、status_label、finalized_kind、finalized_text、stop_reason；底层仍只复用现有 turn_records，不会为了 observer UI 再重跑一遍内部链。",
        )}
        <p class="helper">逐轮 run-local 结果仍看下方 Turn 卡片；这里的 internal chain 只代表当前 bounded autoplay run 的最终快照，不是 per-turn raw dump，也不是 authoritative state。</p>
      </section>
    """


def _build_experimental_one_shot_run_summary_lines(
    *,
    run_result: ExperimentalOneShotRunResult,
) -> list[str]:
    status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        run_result.ending_status,
        run_result.ending_status,
    )
    reason_label = EXPERIMENTAL_ONE_SHOT_ENDING_REASON_LABELS.get(
        run_result.ending_reason,
        run_result.ending_reason,
    )
    lines = [
        f"共自动运行 {len(run_result.turn_records)} 轮 / 最大 {run_result.max_turns} 轮。",
        f"结束状态：{status_label}。",
        f"结束原因：{reason_label}",
    ]
    if run_result.scenario_preset_ending is not None:
        judgment_label = EXPERIMENTAL_PRESET_ENDING_JUDGMENT_LABELS.get(
            run_result.scenario_preset_ending.judgment,
            run_result.scenario_preset_ending.judgment,
        )
        preset_label = _experimental_scenario_preset_label(
            run_result.scenario_preset_ending.preset_id
        )
        lines.append(f"场景 preset：{preset_label}（{run_result.scenario_preset_ending.preset_id}）")
        lines.append(f"场景结局判定：{judgment_label}。")
    if run_result.error_message:
        lines.append(f"错误摘要：{run_result.error_message}")
    if run_result.secret_breach_term:
        lines.append(f"可见侧命中禁区词：{run_result.secret_breach_term}")
    if run_result.narrative_work_note_value:
        lines.append(
            f"最终 run-local narrative_work_note：{_excerpt(run_result.narrative_work_note_value, limit=120)}"
        )
    if run_result.keeper_turn_note_value:
        lines.append(
            f"最终 keeper continuity：{_excerpt(run_result.keeper_turn_note_value, limit=120)}"
        )
    if run_result.visible_turn_note_value:
        lines.append(
            f"最终 visible continuity：{_excerpt(run_result.visible_turn_note_value, limit=120)}"
        )
    lines.append("说明：这只是当前页 run-local transcript / ending summary，不是 authoritative history。")
    return lines


def _render_experimental_one_shot_run_panel(
    *,
    run_result: ExperimentalOneShotRunResult,
    last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
) -> str:
    if not run_result.turn_records:
        return ""
    observer_html = _render_experimental_one_shot_autoplay_observer_panel(
        run_result=run_result,
        last_run_recall=last_run_recall,
    )
    status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        run_result.ending_status,
        run_result.ending_status,
    )
    scenario_preset_html = ""
    if run_result.scenario_preset_ending is not None:
        judgment_label = EXPERIMENTAL_PRESET_ENDING_JUDGMENT_LABELS.get(
            run_result.scenario_preset_ending.judgment,
            run_result.scenario_preset_ending.judgment,
        )
        preset_label = _experimental_scenario_preset_label(
            run_result.scenario_preset_ending.preset_id
        )
        scenario_preset_html = f"""
        <article class="assistant-source-echo">
          <div class="list-head">
            <h3>Scenario Preset Ending Judge</h3>
            <span class="tag warn">{escape(judgment_label)}</span>
          </div>
          <ul class="meta-list">
            <li>preset：{escape(preset_label)}（{escape(run_result.scenario_preset_ending.preset_id)}）</li>
            <li>ending judgment：{escape(judgment_label)}</li>
            <li>ending reason：{escape(run_result.scenario_preset_ending.reason)}</li>
            <li>ending recap：{escape(run_result.scenario_preset_ending.recap)}</li>
          </ul>
          <p class="helper">说明：这只是 preset-based / non-authoritative 的 ending interpretation，用于把当前 demo 收尾说清楚，不代表 scenario truth。</p>
        </article>
        """
    summary_html = "".join(
        f"<li>{escape(line)}</li>"
        for line in _build_experimental_one_shot_run_summary_lines(run_result=run_result)
    )
    transcript_html = "".join(
        f"""
        <article class="assistant-source-echo">
          <div class="list-head">
            <h3>Turn {escape(str(record.turn_index))}</h3>
            <span class="tag">run-local</span>
          </div>
          <ul class="meta-list">
            <li>AI KP：{escape(record.kp_summary or '当前无可用摘要。')}</li>
            <li>AI Investigator：{escape(record.investigator_summary or '当前无可用摘要。')}</li>
            <li>keeper continuity：{escape(record.keeper_continuity or '当前未形成 keeper continuity。')}</li>
            <li>visible continuity：{escape(record.visible_continuity or '当前未形成 visible continuity。')}</li>
            <li>narrative_work_note：{escape(record.narrative_work_note or '当前未形成 run-local narrative note。')}</li>
          </ul>
        </article>
        """
        for record in run_result.turn_records
    )
    return observer_html + f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>One-click Bounded Autopilot Run</h2>
            <p>当前页受控自动 run：只复用 experimental blocks 与 run-local bridge，不会写入 authoritative state，也不是 full AI GM。上方 observer 只展示当前 run 的 very small internal chain 快照。</p>
          </div>
          <span class="tag warn">{escape(status_label)}</span>
        </div>
        <ul class="meta-list">{summary_html}</ul>
        {scenario_preset_html}
        {_render_experimental_demo_collapsible_block(
            section_id="experimental-demo-run-transcript",
            title="完整 run-local transcript",
            badge=f"{len(run_result.turn_records)} turns",
            body_html=transcript_html,
            helper_text="完整 turn transcript 继续保留，但默认收起，避免把首屏挤成长列表。",
        )}
      </section>
    """


def _render_experimental_ai_demo_turn_loop_form(
    *,
    session_id: str,
    selected_investigator_id: str,
    current_turn_index: int,
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    kp_payload: dict[str, str] | None,
    investigator_payload: dict[str, str] | None,
    kp_turn_bridge: dict[str, Any] | None = None,
    investigator_turn_bridge: dict[str, Any] | None = None,
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    keeper_draft_applied: bool = False,
    visible_draft_applied: bool = False,
) -> str:
    if (
        current_turn_index <= 0
        or not selected_investigator_id
        or kp_payload is None
        or investigator_payload is None
        or kp_result is None
        or investigator_result is None
    ):
        return ""
    prior_kp_summary = _normalize_form_text(kp_payload.get("summary")) or "上一轮 AI KP 摘要暂缺。"
    prior_investigator_summary = (
        _normalize_form_text(investigator_payload.get("summary"))
        or "上一轮 AI investigator 摘要暂缺。"
    )
    narrative_note_target_id = _experimental_ai_demo_narrative_work_note_target_id(
        session_id
    )
    keeper_note_target_id = _experimental_ai_demo_keeper_continuity_target_id(session_id)
    visible_note_target_id = _experimental_ai_demo_visible_continuity_target_id(session_id)
    draft_echo_lines: list[str] = []
    if keeper_draft_applied:
        draft_echo_lines.append("已填入 keeper continuity bridge 草稿；仍需人工审阅、修改或清空。")
    if visible_draft_applied:
        draft_echo_lines.append("已填入公开 continuity bridge 草稿；仍需人工审阅、修改或清空。")
    draft_echo_html = (
        f'<article class="assistant-source-echo"><div class="list-head"><h3>当前页草稿填充</h3><span class="tag">drafted</span></div><ul class="meta-list">{"".join(f"<li>{escape(line)}</li>" for line in draft_echo_lines)}</ul></article>'
        if draft_echo_lines
        else ""
    )
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>为下一轮补充上一轮实际结果 / Keeper 采纳情况</h2>
            <p>只在当前实验页内生效。你可以补充上一轮实际发生了什么、采纳了哪些建议，以及公开可见的结果变化，再人工触发下一轮。</p>
          </div>
          <span class="tag warn">page-local only</span>
        </div>
        <ul class="meta-list">
          <li>上一轮 AI KP 摘要：{escape(prior_kp_summary)}</li>
          <li>上一轮 AI investigator 摘要：{escape(prior_investigator_summary)}</li>
          <li>说明：这些补充只作为当前页临时 continuity bridge，不会写入 authoritative state。</li>
        </ul>
        {draft_echo_html}
        {_render_experimental_keeper_drafting_source_echo(
            draft_applied=keeper_draft_applied,
            evaluation_state=evaluation_state,
        )}
        {_render_experimental_visible_drafting_source_echo(
            draft_applied=visible_draft_applied,
        )}
        <form method="post" action="/app/sessions/{escape(session_id)}/experimental-ai-demo/run" class="form-stack">
          <input type="hidden" name="investigator_id" value="{escape(selected_investigator_id)}">
          <input type="hidden" name="current_turn_index" value="{current_turn_index}">
          <input type="hidden" name="current_kp_result_json" value="{escape(_assistant_result_hidden_json(kp_result))}">
          <input type="hidden" name="current_investigator_result_json" value="{escape(_assistant_result_hidden_json(investigator_result))}">
          <input type="hidden" name="current_kp_has_keeper_continuity" value="{1 if kp_turn_bridge and _normalize_form_text(kp_turn_bridge.get('keeper_adoption_and_outcome_note')) else 0}">
          <input type="hidden" name="current_kp_has_visible_continuity" value="{1 if kp_turn_bridge and _normalize_form_text(kp_turn_bridge.get('public_outcome_note')) else 0}">
          <input type="hidden" name="current_investigator_has_visible_continuity" value="{1 if investigator_turn_bridge and _normalize_form_text(investigator_turn_bridge.get('public_outcome_note')) else 0}">
          <input type="hidden" name="previous_kp_title" value="{escape(kp_payload.get('title') or '')}">
          <input type="hidden" name="previous_kp_summary" value="{escape(kp_payload.get('summary') or '')}">
          <input type="hidden" name="previous_kp_draft_excerpt" value="{escape(kp_payload.get('draft_excerpt') or '')}">
          <input type="hidden" name="previous_investigator_title" value="{escape(investigator_payload.get('title') or '')}">
          <input type="hidden" name="previous_investigator_summary" value="{escape(investigator_payload.get('summary') or '')}">
          <input type="hidden" name="previous_investigator_draft_excerpt" value="{escape(investigator_payload.get('draft_excerpt') or '')}">
          {_render_hidden_experimental_demo_evaluation_state_inputs(evaluation_state)}
          <label>
            当前页 narrative_work_note
            <textarea id="{escape(narrative_note_target_id)}" name="narrative_work_note" rows="4" placeholder="例如：先用潮气、旧账册和老板的短暂失态起场，再把压力推向 204 房登记与二楼动静。">{escape(narrative_work_note_value)}</textarea>
          </label>
          <p class="helper">只用于 Keeper 在当前 experimental 页整理 preview narrative handoff；这是 page-local working text，不是已执行结果，也不会写入 authoritative state。</p>
          <label>
            上一轮实际结果 / Keeper 采纳情况（仅 AI KP 可见）
            <textarea id="{escape(keeper_note_target_id)}" name="keeper_turn_outcome_note" rows="4" placeholder="例如：实际让秦老板先否认，再露出对 204 房登记的回避；当前压力从缺页账册转到二楼动静。">{escape(keeper_turn_note_value)}</textarea>
          </label>
          <p class="helper">这是当前页 keeper continuity working text，只供 Keeper 人工补充、覆写与下一轮实验输入使用；不会自动提交，也不会写入 authoritative state。</p>
          <label>
            上一轮公开可见结果摘要（供 AI Investigator 下一轮使用）
            <textarea id="{escape(visible_note_target_id)}" name="visible_turn_outcome_note" rows="3" placeholder="例如：老板回避了 204 房登记，调查员注意到账册缺页和二楼脚步声。">{escape(visible_turn_note_value)}</textarea>
          </label>
          <p class="helper">这是当前页 visible continuity working text，只能带入公开可见内容；仍需 Keeper 人工编辑，且不会自动提交或写入 authoritative state。</p>
          <button class="button-button ghost" type="submit" formaction="/app/sessions/{escape(session_id)}/experimental-ai-demo/draft-continuity">起草 continuity bridge 草稿</button>
          <button class="button-button ghost" type="submit" formaction="/app/sessions/{escape(session_id)}/experimental-ai-demo/self-play-preview">运行 self-play 预演链</button>
          <button class="button-button secondary" type="submit">生成下一轮实验回合</button>
        </form>
      </section>
    """


def _render_experimental_ai_demo_evaluation_rubric(
    *,
    session_id: str,
    selected_investigator_id: str,
    current_turn_index: int,
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    kp_turn_bridge: dict[str, Any] | None = None,
    investigator_turn_bridge: dict[str, Any] | None = None,
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    keeper_draft_applied: bool = False,
    visible_draft_applied: bool = False,
) -> str:
    if (
        current_turn_index <= 0
        or not selected_investigator_id
        or kp_result is None
        or investigator_result is None
    ):
        return ""
    evaluation_state = dict(evaluation_state or {})
    options_html = "".join(
        f'<option value="{escape(value)}"{{selected}}>{escape(label)}</option>'
        for value, label in EXPERIMENTAL_DEMO_RUBRIC_VALUE_LABELS.items()
    )
    field_html_parts: list[str] = []
    summary_items: list[str] = []
    for field_name, field_label in EXPERIMENTAL_DEMO_RUBRIC_FIELD_LABELS.items():
        selected_value = evaluation_state.get(field_name, "")
        selected_label = EXPERIMENTAL_DEMO_RUBRIC_VALUE_LABELS.get(selected_value)
        rendered_options = options_html.replace(
            "{selected}",
            "",
        )
        if selected_value and selected_label:
            rendered_options = "".join(
                (
                    f'<option value="">未评</option>',
                    "".join(
                        f'<option value="{escape(value)}"{" selected" if value == selected_value else ""}>{escape(label)}</option>'
                        for value, label in EXPERIMENTAL_DEMO_RUBRIC_VALUE_LABELS.items()
                    ),
                )
            )
            summary_items.append(f"<li>{escape(field_label)}：{escape(selected_label)}</li>")
        else:
            rendered_options = "".join(
                (
                    '<option value="" selected>未评</option>',
                    "".join(
                        f'<option value="{escape(value)}">{escape(label)}</option>'
                        for value, label in EXPERIMENTAL_DEMO_RUBRIC_VALUE_LABELS.items()
                    ),
                )
            )
        field_html_parts.append(
            f"""
            <label>
              {escape(field_label)}
              <select name="{escape(field_name)}">
                {rendered_options}
              </select>
            </label>
            """
        )
    evaluation_label = evaluation_state.get("evaluation_label", "")
    evaluation_note = evaluation_state.get("evaluation_note", "")
    summary_html = ""
    if summary_items or evaluation_note or evaluation_label:
        label_html = (
            f"<p class=\"helper\">当前实验标签：{escape(evaluation_label)}</p>"
            if evaluation_label
            else ""
        )
        note_html = (
            f"<p class=\"helper\">备注：{escape(evaluation_note)}</p>"
            if evaluation_note
            else ""
        )
        summary_html = f"""
          <div class="card-list">
            <article class="assistant-source-echo">
              <div class="list-head">
                <h3>当前页评估回显</h3>
                <span class="tag">keeper review</span>
              </div>
              {label_html}
              <ul class="meta-list">{''.join(summary_items)}</ul>
              {note_html}
            </article>
          </div>
        """
    return f"""
      <section class="surface">
        <div class="surface-header">
          <div>
            <h2>当前页实验评估</h2>
            <p>只用于 keeper 比较当前轮或当前次实验的质量，不会写入 authoritative state，也不会跨刷新持久化。</p>
          </div>
          <span class="tag warn">page-local rubric</span>
        </div>
        {summary_html}
        <form method="post" action="/app/sessions/{escape(session_id)}/experimental-ai-demo/evaluate" class="form-stack">
          <input type="hidden" name="investigator_id" value="{escape(selected_investigator_id)}">
          <input type="hidden" name="current_turn_index" value="{current_turn_index}">
          <input type="hidden" name="current_kp_result_json" value="{escape(_assistant_result_hidden_json(kp_result))}">
          <input type="hidden" name="current_investigator_result_json" value="{escape(_assistant_result_hidden_json(investigator_result))}">
          <input type="hidden" name="current_kp_has_keeper_continuity" value="{1 if kp_turn_bridge and _normalize_form_text(kp_turn_bridge.get('keeper_adoption_and_outcome_note')) else 0}">
          <input type="hidden" name="current_kp_has_visible_continuity" value="{1 if kp_turn_bridge and _normalize_form_text(kp_turn_bridge.get('public_outcome_note')) else 0}">
          <input type="hidden" name="current_investigator_has_visible_continuity" value="{1 if investigator_turn_bridge and _normalize_form_text(investigator_turn_bridge.get('public_outcome_note')) else 0}">
          <input type="hidden" name="current_narrative_work_note" value="{escape(narrative_work_note_value)}">
          <input type="hidden" name="current_keeper_turn_outcome_note" value="{escape(keeper_turn_note_value)}">
          <input type="hidden" name="current_visible_turn_outcome_note" value="{escape(visible_turn_note_value)}">
          <input type="hidden" name="current_keeper_draft_applied" value="{1 if keeper_draft_applied else 0}">
          <input type="hidden" name="current_visible_draft_applied" value="{1 if visible_draft_applied else 0}">
          <label>
            当前实验标签 / 比较说明（可选）
            <input type="text" name="evaluation_label" value="{escape(evaluation_label)}" maxlength="120" placeholder="例如：continuity 写法 2 / 更激进的 KP framing / temp 低">
          </label>
          {''.join(field_html_parts)}
          <label>
            评估备注（可选）
            <textarea name="evaluation_note" rows="3" placeholder="例如：KP 开场更稳了，但 investigator 提案第二轮开始略重复。">{escape(evaluation_note)}</textarea>
          </label>
          <button class="button-button secondary" type="submit">记录当前页评估</button>
        </form>
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
        <h1>Local Web App Shell</h1>
        <p>Launcher 只负责打开本地服务与 demo 入口；sessions / keeper / investigator / demo workspace 才是主要 workflow 表面。</p>
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


def _is_demo_boot_enabled(raw_value: Any) -> bool:
    normalized = (_normalize_form_text(raw_value) or "").lower()
    return normalized in {"1", "true", "yes", "on"}


DEMO_BOOT_PLAYTEST_GROUP = "内部 Observer Demo"


def _build_demo_boot_setup_form_values(
    *,
    playtest_group: str | None = None,
) -> dict[str, Any]:
    values = _default_playtest_setup_form_values()
    values["keeper_name"] = "内部演示KP"
    values["playtest_group"] = _normalize_form_text(playtest_group) or DEMO_BOOT_PLAYTEST_GROUP
    values["scenario_template"] = "whispering_guesthouse"
    values["investigator_names"] = ["演示调查员", "", "", ""]
    return values


def _is_reusable_demo_boot_session(session: dict[str, Any]) -> bool:
    if _normalize_form_text(session.get("playtest_group")) != DEMO_BOOT_PLAYTEST_GROUP:
        return False
    session_id = _normalize_form_text(session.get("session_id")) or ""
    if not session_id:
        return False
    participants = [
        participant
        for participant in session.get("participants") or []
        if isinstance(participant, dict)
    ]
    return bool(
        _demo_investigator_candidates(
            participants,
            keeper_id=session.get("keeper_id"),
        )
    )


def _resolve_recent_demo_boot_session_id(sessions: list[dict[str, Any]]) -> str | None:
    for session in sessions:
        if _is_reusable_demo_boot_session(session):
            return _normalize_form_text(session.get("session_id")) or None
    return None


def _experimental_ai_demo_session_boot_href(session_id: str) -> str:
    return (
        f"/app/sessions/{session_id}/experimental-ai-demo"
        "?demo_boot=1#experimental-demo-one-shot-control"
    )


def _experimental_ai_demo_setup_boot_href(*, fresh: bool = False) -> str:
    return "/app/setup?demo_boot=1&fresh=1" if fresh else "/app/setup?demo_boot=1"


def _build_experimental_autopilot_last_run_recall_from_run_result(
    run_result: ExperimentalOneShotRunResult,
) -> ExperimentalAutopilotLastRunRecall:
    provider_name = ""
    model = ""
    for result in (
        run_result.kp_result,
        run_result.investigator_result,
        run_result.keeper_draft_result,
        run_result.visible_draft_result,
    ):
        if result is None:
            continue
        if result.provider_name or result.model:
            provider_name = _normalize_form_text(result.provider_name) or ""
            model = _normalize_form_text(result.model) or ""
            break
    return ExperimentalAutopilotLastRunRecall(
        ending_status=run_result.ending_status,
        ending_reason=run_result.ending_reason,
        provider_name=provider_name,
        model=model,
    )


def _read_experimental_autopilot_last_run_recall(
    source: Mapping[str, Any],
) -> ExperimentalAutopilotLastRunRecall | None:
    ending_status = _normalize_form_text(source.get("last_run_status")) or ""
    if not ending_status:
        return None
    return ExperimentalAutopilotLastRunRecall(
        ending_status=ending_status,
        ending_reason=_normalize_form_text(source.get("last_run_reason")) or "",
        provider_name=_normalize_form_text(source.get("last_run_provider")) or "",
        model=_normalize_form_text(source.get("last_run_model")) or "",
    )


def _experimental_autopilot_last_run_recall_query_params(
    recall: ExperimentalAutopilotLastRunRecall | None,
) -> dict[str, str]:
    if recall is None:
        return {}
    params = {
        "last_run_status": recall.ending_status,
        "last_run_reason": recall.ending_reason,
    }
    if recall.provider_name:
        params["last_run_provider"] = recall.provider_name
    if recall.model:
        params["last_run_model"] = recall.model
    return params


def _append_query_params(url: str, params: Mapping[str, str]) -> str:
    normalized_params = {
        key: value
        for key, value in params.items()
        if _normalize_form_text(value)
    }
    if not normalized_params:
        return url
    split = urlsplit(url)
    query_items = dict(parse_qsl(split.query, keep_blank_values=True))
    query_items.update(normalized_params)
    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(query_items),
            split.fragment,
        )
    )


def _experimental_ai_demo_session_boot_href_with_recall(
    session_id: str,
    *,
    recall: ExperimentalAutopilotLastRunRecall | None = None,
) -> str:
    return _append_query_params(
        _experimental_ai_demo_session_boot_href(session_id),
        _experimental_autopilot_last_run_recall_query_params(recall),
    )


def _experimental_ai_demo_setup_boot_href_with_recall(
    *,
    fresh: bool = False,
    recall: ExperimentalAutopilotLastRunRecall | None = None,
) -> str:
    return _append_query_params(
        _experimental_ai_demo_setup_boot_href(fresh=fresh),
        _experimental_autopilot_last_run_recall_query_params(recall),
    )


def _render_experimental_autopilot_last_run_recall_hidden_inputs(
    recall: ExperimentalAutopilotLastRunRecall | None,
) -> str:
    if recall is None:
        return ""
    params = _experimental_autopilot_last_run_recall_query_params(recall)
    return "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(value)}">'
        for name, value in params.items()
    )


def _render_experimental_autopilot_last_run_recall_surface(
    recall: ExperimentalAutopilotLastRunRecall | None,
) -> str:
    if recall is None:
        return ""
    recall_copy = _build_experimental_autopilot_last_run_copy(recall)
    runtime_html = ""
    if recall_copy.runtime_text:
        runtime_html = f"<li>{escape(recall_copy.runtime_text)}</li>"
    return f"""
      <article id="experimental-demo-last-run-token-history" class="assistant-source-echo">
        <div class="list-head">
          <h3>Last Autopilot Recall</h3>
          <span class="tag">last run</span>
        </div>
        <ul class="meta-list">
          <li>{escape(recall_copy.status_text)}</li>
          <li>{escape(recall_copy.stop_reason_text)}</li>
          {runtime_html}
        </ul>
        <p class="helper">只保留最近一次 autopilot run 的 very small recall，不是 full runtime history，也不是 diagnostics dashboard。</p>
      </article>
    """


def _build_experimental_autopilot_last_run_copy(
    recall: ExperimentalAutopilotLastRunRecall,
) -> ExperimentalAutopilotRuntimeCopy:
    status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        recall.ending_status,
        recall.ending_status,
    )
    reason_label = EXPERIMENTAL_ONE_SHOT_ENDING_REASON_LABELS.get(
        recall.ending_reason,
        recall.ending_reason,
    )
    return _build_experimental_autopilot_runtime_copy(
        subject_label="上一轮",
        status_label=status_label,
        reason_label=reason_label or "未记录",
        provider_name=recall.provider_name,
        model=recall.model,
    )


def _build_experimental_autopilot_runtime_copy(
    *,
    subject_label: str,
    status_label: str,
    reason_label: str | None = None,
    provider_name: str = "",
    model: str = "",
) -> ExperimentalAutopilotRuntimeCopy:
    runtime_parts: list[str] = []
    if provider_name:
        runtime_parts.append(f"provider：{provider_name}")
    if model:
        runtime_parts.append(f"model：{model}")
    runtime_text = ""
    if runtime_parts:
        runtime_text = f"{subject_label} runtime：" + " / ".join(runtime_parts)
    stop_reason_text = ""
    if reason_label:
        stop_reason_text = f"{subject_label}停止原因：{reason_label}"
    return ExperimentalAutopilotRuntimeCopy(
        status_text=f"{subject_label}状态：{status_label}",
        stop_reason_text=stop_reason_text,
        runtime_text=runtime_text,
    )


def _render_experimental_observer_last_run_recall_row(
    recall: ExperimentalAutopilotLastRunRecall | None,
) -> str:
    if recall is None:
        return ""
    recall_copy = _build_experimental_autopilot_last_run_copy(recall)
    runtime_html = ""
    if recall_copy.runtime_text:
        runtime_html = (
            '<span class="experimental-observer-recall-item">'
            f"{escape(recall_copy.runtime_text)}"
            "</span>"
        )
    return f"""
      <div id="experimental-demo-observer-last-run-recall" class="experimental-observer-recall-strip">
        <span class="tag">last run</span>
        <span class="experimental-observer-recall-item">{escape(recall_copy.status_text)}</span>
        <span class="experimental-observer-recall-item">{escape(recall_copy.stop_reason_text)}</span>
        {runtime_html}
        <span class="experimental-observer-recall-item">single-entry recall，不是 history system，也不是 diagnostics dashboard。</span>
      </div>
    """


def _render_setup_page(
    *,
    form_values: dict[str, Any] | None = None,
    demo_boot: bool = False,
    last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
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
    if demo_boot:
        actions.insert(0, ("普通 Setup", "/app/setup", "ghost"))
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
    page_title = "在 App Shell 内创建新局"
    page_summary = (
        "create / setup 已收进 Web GUI 壳，仍复用现有 playtest 模板和 start_session 语义，不新开后端产品线。"
    )
    section_title = "最小 setup"
    section_summary = "保持现有模板建局语义，只把入口、层级和回链收进统一 app shell。"
    helper_text = "至少填写 1 名调查员。创建成功后直接进入新的 session overview，而不是跳回旧 launcher。"
    submit_label = "创建并进入 App Shell"
    hidden_fields_html = ""
    demo_boot_intro_html = ""
    if demo_boot:
        page_title = "创建 Demo Session 并进入 Bounded Autopilot Demo"
        page_summary = (
            "launcher / exe demo boot 只补一条 internal 引导链：复用现有模板建局与 bounded autopilot run，"
            "让 keeper 不必先翻 Session 列表再找 experimental observer。"
        )
        section_title = "Demo-ready setup"
        section_summary = (
            "只为 internal observer demo 预填 very small sample session：创建后直接进入 bounded autopilot observer，"
            "不扩成最终产品级 launcher 或 full autopilot runtime。"
        )
        helper_text = (
            "这条入口只服务 keeper/internal demo boot。提交后会先创建 sample session，"
            "再直接复用现有 bounded autopilot 主链展示 observer autoplay run；不会自动改写该 session 的 authoritative 历史。"
        )
        submit_label = "创建 Demo Session 并运行 Bounded Autopilot"
        hidden_fields_html = """
            <input type="hidden" name="demo_boot" value="1" />
            <input type="hidden" name="launch_target" value="experimental_ai_demo" />
            <input type="hidden" name="autorun_one_shot" value="1" />
        """
        hidden_fields_html += _render_experimental_autopilot_last_run_recall_hidden_inputs(
            last_run_recall
        )
        demo_boot_intro_html = """
        <section class="notice-panel">
          <h2>Launcher Demo Boot</h2>
          <p>当前是 keeper/internal 的 demo boot 引导页。默认使用 sample template 与 sample investigator；点击一次即可创建 demo session 并直接看到 bounded autopilot observer 的过程与结果。</p>
        </section>
        """
    body = (
        _page_head(
            eyebrow="Setup / Create",
            title=page_title,
            summary=page_summary,
            actions=actions,
        )
        + demo_boot_intro_html
        + _detail_block(detail)
        + f"""
        <section class="surface">
          <div class="surface-header">
            <div>
              <h2>{escape(section_title)}</h2>
              <p>{escape(section_summary)}</p>
            </div>
          </div>
          <form method="post" action="/app/setup" class="form-stack">
            {hidden_fields_html}
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
            <p class="helper">{escape(helper_text)}</p>
            <button class="button-button" type="submit">{escape(submit_label)}</button>
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
    context_pack: dict[str, Any] | None = None,
    compressed_context: dict[str, Any] | None = None,
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
          extra_output_html=(
              _render_compressed_context_source_echo(
                  result=narrative_result,
                  compressed_context=compressed_context,
                  suggestion_label="剧情支架建议",
              )
              + _render_context_pack_source_echo(
                  result=narrative_result,
                  context_pack=context_pack,
                  suggestion_label="剧情支架建议",
              )
              + _render_assistant_draft_source(
                  assistant_scope=narrative_scope,
                  assistant_adoption=narrative_adoption,
              )
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
    compressed_context: dict[str, Any] | None = None,
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
                ("Experimental AI Demo", f"/app/sessions/{session_id}/experimental-ai-demo", "ghost"),
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
                _render_keeper_compressed_context_block(compressed_context=compressed_context)
                if compressed_context is not None
                else ""
            }
            {
                _render_keeper_context_pack_block(context_pack=context_pack)
                if context_pack is not None
                else ""
            }
            {_render_keeper_narrative_scaffolding(
                session_id=session_id,
                snapshot=snapshot,
                context_pack=context_pack,
                compressed_context=compressed_context,
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
    compressed_context: dict[str, Any] | None = None,
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
                _render_keeper_compressed_context_block(
                    compressed_context=compressed_context,
                    title="Compact Recap / 压缩工作摘要",
                    summary="把 keeper-side compact recap 固定挂在 recap 页，便于 closeout 回看，并优先供 recap assistant 与 future AI demo 复用。",
                )
                if compressed_context is not None
                else ""
            }
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
                extra_output_html=(
                    _render_compressed_context_source_echo(
                        result=assistant_result,
                        compressed_context=compressed_context,
                        suggestion_label="recap 建议",
                    )
                    + _render_context_pack_source_echo(
                        result=assistant_result,
                        context_pack=context_pack,
                        suggestion_label="recap 建议",
                    )
                ),
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


def _render_experimental_ai_demo_page(
    *,
    session_id: str,
    snapshot: dict[str, Any],
    compressed_context: dict[str, Any],
    investigator_candidates: list[dict[str, Any]],
    selected_investigator_id: str | None,
    selected_investigator_view: dict[str, Any] | None,
    kp_result: LocalLLMAssistantResult | None,
    investigator_result: LocalLLMAssistantResult | None,
    current_turn_index: int = 0,
    kp_turn_bridge: dict[str, Any] | None = None,
    investigator_turn_bridge: dict[str, Any] | None = None,
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    keeper_draft_applied: bool = False,
    visible_draft_applied: bool = False,
    orchestration_preview_html: str = "",
    one_shot_max_turns: int = EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS,
    last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
    one_shot_run_result: ExperimentalOneShotRunResult | None = None,
    one_shot_run_html: str = "",
    demo_boot: bool = False,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
) -> HTMLResponse:
    initial_run_button_label = (
        "重新基于当前主状态运行单轮实验"
        if current_turn_index > 0
        else "运行第 1 轮实验回合"
    )
    kp_turn_payload = _assistant_result_turn_bridge_payload(kp_result)
    investigator_turn_payload = _assistant_result_turn_bridge_payload(investigator_result)
    options_html = "".join(
        f'<option value="{escape(str(item.get("actor_id") or ""))}"{" selected" if str(item.get("actor_id") or "") == (selected_investigator_id or "") else ""}>'
        f'{escape(str(item.get("display_name") or item.get("actor_id") or "调查员"))}'
        f'（{escape(str(item.get("kind") or "unknown"))}）</option>'
        for item in investigator_candidates
    )
    autopilot_token_surface_html = _render_experimental_ai_demo_autopilot_token_surface(
        token_surface=_build_experimental_ai_demo_autopilot_token_surface(
            run_result=one_shot_run_result,
        )
    )
    last_run_recall_html = _render_experimental_autopilot_last_run_recall_surface(
        last_run_recall
    )
    next_last_run_recall = (
        _build_experimental_autopilot_last_run_recall_from_run_result(
            one_shot_run_result
        )
        if one_shot_run_result is not None
        else last_run_recall
    )
    last_run_recall_hidden_inputs_html = (
        _render_experimental_autopilot_last_run_recall_hidden_inputs(
            next_last_run_recall
        )
    )
    primary_controls_html = _render_experimental_ai_demo_primary_controls(
        session_id=session_id,
        options_html=options_html,
        initial_run_button_label=initial_run_button_label,
        one_shot_max_turns=one_shot_max_turns,
        autopilot_token_surface_html=autopilot_token_surface_html,
        last_run_recall_html=last_run_recall_html,
        last_run_recall_hidden_inputs_html=last_run_recall_hidden_inputs_html,
        demo_boot=demo_boot,
        selected_investigator_id=selected_investigator_id or "",
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_turn_note_value,
        visible_turn_note_value=visible_turn_note_value,
    )
    primary_observer_html = (
        one_shot_run_html
        or _render_experimental_one_shot_autoplay_observer_panel(
            last_run_recall=last_run_recall
        )
    )
    turn_loop_html = _render_experimental_ai_demo_turn_loop_form(
        session_id=session_id,
        selected_investigator_id=selected_investigator_id or "",
        current_turn_index=current_turn_index,
        kp_result=kp_result,
        investigator_result=investigator_result,
        kp_payload=kp_turn_payload,
        investigator_payload=investigator_turn_payload,
        kp_turn_bridge=kp_turn_bridge,
        investigator_turn_bridge=investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_turn_note_value,
        visible_turn_note_value=visible_turn_note_value,
        keeper_draft_applied=keeper_draft_applied,
        visible_draft_applied=visible_draft_applied,
    )
    secondary_inputs_html = _render_experimental_demo_collapsible_surface(
        section_id="experimental-demo-secondary-inputs",
        title="输入摘要与上下文",
        summary="低频输入区：AI KP 的 compressed context 与 AI investigator 的 visible-only 输入摘要。",
        badge="secondary inputs",
        helper_text="这些输入块仍然保留，但默认后移，避免首屏被输入说明挤占。",
        body_html=f"""
          <div class="card-list">
            {_render_keeper_compressed_context_block(
                compressed_context=compressed_context,
                title="AI KP 输入：Compressed Context",
                summary="AI KP 只吃 keeper-side compact recap / compressed context，不直接读取主状态全量 dump。",
            )}
            {_render_experimental_investigator_input_block(
                investigator_view=selected_investigator_view,
            )}
          </div>
        """,
    )
    secondary_outputs_html = _render_experimental_demo_collapsible_surface(
        section_id="experimental-demo-secondary-outputs",
        title="实验输出详情",
        summary="低频输出区：当前轮 AI KP / AI investigator 的候选输出与来源回显。",
        badge="secondary outputs",
        helper_text="输出能力未删除，只是默认收进折叠区；Keeper 需要细看时再展开。",
        body_html=f"""
          <div class="card-list">
            {_render_experimental_ai_demo_output_block(
                title="AI KP Demo Output",
                summary="候选的 scene framing / pressure / next beat / NPC reaction 草稿，仅供 Keeper 比较。",
                result=kp_result,
                source_echo_html=(
                    _render_experimental_demo_source_echo(
                        result=kp_result,
                        title="AI KP 输入来源",
                        lines=[
                            "本次 AI KP 实验输出仅基于 keeper-side Compressed Context 与最多 3 条近期事件摘要。",
                            "说明：这是 experimental / non-authoritative narration draft，不会自动推进 session。",
                        ],
                    )
                    + _render_experimental_kp_continuity_source_echo(
                        result=kp_result,
                        turn_bridge=kp_turn_bridge,
                    )
                ),
            )}
            {_render_experimental_ai_demo_output_block(
                title="AI Investigator Demo Output",
                summary="候选的调查员行动提案、行动理由与可继续追问方向，仅供观察 AI investigator reasoning loop。",
                result=investigator_result,
                source_echo_html=(
                    _render_experimental_demo_source_echo(
                        result=investigator_result,
                        title="AI Investigator 输入来源",
                        lines=[
                            "本次 AI investigator 实验输出只基于所选调查员的可见状态摘要。",
                            "输入范围：可见场景、可见线索、最近事件、角色状态与战斗摘要，不含 keeper-only 信息。",
                            "说明：这是 experimental / non-authoritative action proposal，不会自动执行。",
                        ],
                    )
                    + _render_experimental_investigator_continuity_source_echo(
                        result=investigator_result,
                        turn_bridge=investigator_turn_bridge,
                    )
                ),
            )}
          </div>
        """,
    )
    secondary_preview_html = _render_experimental_demo_collapsible_surface(
        section_id="experimental-demo-secondary-preview",
        title="Self-play 预演链详情",
        summary="低频预演区：串行 preview AI KP、AI investigator、keeper continuity draft 与 visible continuity draft。",
        badge="preview chain",
        helper_text="预演链继续保留，但默认收起，避免首屏被 step-by-step 预演明细占满。",
        body_html=orchestration_preview_html,
    )
    secondary_evaluation_html = _render_experimental_demo_collapsible_surface(
        section_id="experimental-demo-secondary-rubric",
        title="当前页实验评估",
        summary="低频评估区：只用于 Keeper 对当前页实验做 page-local rubric review。",
        badge="page-local rubric",
        helper_text="这仍是 page-local keeper review，不会写入 authoritative state。",
        body_html=_render_experimental_ai_demo_evaluation_rubric(
            session_id=session_id,
            selected_investigator_id=selected_investigator_id or "",
            current_turn_index=current_turn_index,
            kp_result=kp_result,
            investigator_result=investigator_result,
            kp_turn_bridge=kp_turn_bridge,
            investigator_turn_bridge=investigator_turn_bridge,
            evaluation_state=evaluation_state,
            narrative_work_note_value=narrative_work_note_value,
            keeper_turn_note_value=keeper_turn_note_value,
            visible_turn_note_value=visible_turn_note_value,
            keeper_draft_applied=keeper_draft_applied,
            visible_draft_applied=visible_draft_applied,
        ),
    )
    primary_workspace_html = (
        f"""
        <section id="experimental-demo-primary-workspace" class="experimental-primary-workspace two-column">
          <div class="card-list">
            {primary_observer_html}
          </div>
          <div class="card-list">
            {turn_loop_html}
          </div>
        </section>
        """
        if turn_loop_html
        else f"""
        <section id="experimental-demo-primary-workspace" class="experimental-primary-workspace">
          <div class="card-list">
            {primary_observer_html}
          </div>
        </section>
        """
    )
    body = (
        _page_head(
            eyebrow="Local Web App Shell",
            title="AI KP + AI Investigator Demo Harness",
            summary="当前本地 Web App Shell 的受控 demo 操作台：把 launcher demo boot、bounded autoplay observer、单轮实验与 rerun / fresh 回链收进同一工作表面。仍是 experimental / non-authoritative，不是 full autopilot runtime。",
            actions=[
                (
                    "Demo Setup / Fresh",
                    _experimental_ai_demo_setup_boot_href_with_recall(
                        fresh=True,
                        recall=next_last_run_recall,
                    ),
                    "secondary",
                ),
                ("Keeper Workspace", f"/app/sessions/{session_id}/keeper", "ghost"),
                ("Session Overview", f"/app/sessions/{session_id}", "ghost"),
            ],
        )
        + _notice_block(notice)
        + _detail_block(detail)
        + _render_experimental_ai_demo_workspace_strip(
            session_id=session_id,
            investigator_candidates=investigator_candidates,
            selected_investigator_id=selected_investigator_id,
            current_turn_index=current_turn_index,
            controls_html=primary_controls_html,
            next_last_run_recall=next_last_run_recall,
            demo_boot=demo_boot,
            one_shot_run_visible=bool(one_shot_run_html),
        )
        + primary_workspace_html
        + secondary_inputs_html
        + secondary_outputs_html
        + secondary_preview_html
        + secondary_evaluation_html
    )
    return render_web_app_shell(
        title=f"Session {session_id} Experimental AI Demo",
        sidebar_html=_render_sidebar(active_section="keeper", session_snapshot=snapshot),
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
    compressed_context: dict[str, Any] | None = None,
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
    if compressed_context is None:
        compressed_context = _build_keeper_compressed_context_payload(
            service=service,
            context_pack=context_pack,
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
        compressed_context=compressed_context,
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
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
    )
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
        compressed_context=compressed_context,
        assistant_result=assistant_result,
        selected_assistant_task=selected_assistant_task,
    )
    response.status_code = status_code
    return response


def _render_app_experimental_ai_demo_from_service(
    *,
    service: SessionService,
    session_id: str,
    local_llm_service: LocalLLMService | None = None,
    selected_investigator_id: str | None = None,
    kp_result: LocalLLMAssistantResult | None = None,
    investigator_result: LocalLLMAssistantResult | None = None,
    current_turn_index: int = 0,
    kp_turn_bridge: dict[str, Any] | None = None,
    investigator_turn_bridge: dict[str, Any] | None = None,
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    keeper_draft_applied: bool = False,
    visible_draft_applied: bool = False,
    orchestration_preview_html: str = "",
    one_shot_max_turns: int = EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS,
    last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
    one_shot_run_result: ExperimentalOneShotRunResult | None = None,
    one_shot_run_html: str = "",
    demo_boot: bool = False,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Experimental AI Demo",
            title="Experimental AI Demo 不可用",
            summary="当前无法加载实验 harness 所需的 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Experimental AI Demo Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    candidates = _demo_investigator_candidates(
        participants,
        keeper_id=snapshot.get("keeper_id"),
    )
    resolved_investigator_id = selected_investigator_id
    if not resolved_investigator_id and candidates:
        resolved_investigator_id = str(candidates[0].get("actor_id") or "")
    selected_investigator_view: dict[str, Any] | None = None
    if resolved_investigator_id:
        try:
            selected_investigator_view = service.get_session_view(
                session_id,
                viewer_id=resolved_investigator_id,
                viewer_role=ViewerRole.INVESTIGATOR,
            ).model_dump(mode="json")
        except LookupError:
            detail = detail or f"当前无法定位 investigator 视角 {resolved_investigator_id}。"
            status_code = status.HTTP_404_NOT_FOUND
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
    ).model_dump(mode="json")
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
    )
    if kp_result is None and local_llm_service is not None and not local_llm_service.enabled:
        kp_result = _disabled_assistant_result(
            workspace_key="experimental_ai_kp_demo",
            tasks=EXPERIMENTAL_AI_KP_DEMO_TASKS,
            selected_task="demo_loop",
        )
    if investigator_result is None and local_llm_service is not None and not local_llm_service.enabled:
        investigator_result = _disabled_assistant_result(
            workspace_key="experimental_ai_investigator_demo",
            tasks=EXPERIMENTAL_AI_INVESTIGATOR_DEMO_TASKS,
            selected_task="demo_loop",
        )
    response = _render_experimental_ai_demo_page(
        session_id=session_id,
        snapshot=snapshot,
        compressed_context=compressed_context,
        investigator_candidates=candidates,
        selected_investigator_id=resolved_investigator_id,
        selected_investigator_view=selected_investigator_view,
        kp_result=kp_result,
        investigator_result=investigator_result,
        current_turn_index=current_turn_index,
        kp_turn_bridge=kp_turn_bridge,
        investigator_turn_bridge=investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_turn_note_value,
        visible_turn_note_value=visible_turn_note_value,
        keeper_draft_applied=keeper_draft_applied,
        visible_draft_applied=visible_draft_applied,
        orchestration_preview_html=orchestration_preview_html,
        one_shot_max_turns=one_shot_max_turns,
        last_run_recall=last_run_recall,
        one_shot_run_result=one_shot_run_result,
        one_shot_run_html=one_shot_run_html,
        demo_boot=demo_boot,
        notice=notice,
        detail=detail,
    )
    response.status_code = status_code
    return response


def _render_experimental_ai_demo_one_shot_run_from_service(
    *,
    service: SessionService,
    local_llm_service: LocalLLMService,
    session_id: str,
    selected_investigator_id: str = "",
    max_turns: int = EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS,
    last_run_recall: ExperimentalAutopilotLastRunRecall | None = None,
    evaluation_state: Mapping[str, str] | None = None,
    narrative_work_note_value: str = "",
    keeper_turn_note_value: str = "",
    visible_turn_note_value: str = "",
    demo_boot: bool = False,
    notice_prefix: str | None = None,
) -> HTMLResponse:
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Experimental AI Demo",
            title="Experimental AI Demo 不可用",
            summary="当前无法加载实验 harness 所需的 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Experimental AI Demo Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    candidates = _demo_investigator_candidates(
        participants,
        keeper_id=snapshot.get("keeper_id"),
    )
    candidate_ids = {
        str(item.get("actor_id") or "")
        for item in candidates
        if str(item.get("actor_id") or "")
    }
    resolved_investigator_id = selected_investigator_id
    if not resolved_investigator_id and candidates:
        resolved_investigator_id = str(candidates[0].get("actor_id") or "")
    if not resolved_investigator_id or resolved_investigator_id not in candidate_ids:
        return _render_app_experimental_ai_demo_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            selected_investigator_id=resolved_investigator_id,
            last_run_recall=last_run_recall,
            one_shot_max_turns=max_turns,
            demo_boot=demo_boot,
            narrative_work_note_value=narrative_work_note_value,
            keeper_turn_note_value=keeper_turn_note_value,
            visible_turn_note_value=visible_turn_note_value,
            detail="当前无法建立可用的 investigator visible summary；请先选择一个有效调查员视角。",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    investigator_view = service.get_session_view(
        session_id,
        viewer_id=resolved_investigator_id,
        viewer_role=ViewerRole.INVESTIGATOR,
    ).model_dump(mode="json")
    run_result = _run_experimental_one_shot_demo(
        service=service,
        local_llm_service=local_llm_service,
        session=session,
        keeper_view=keeper_view,
        snapshot=snapshot,
        investigator_view=investigator_view,
        investigator_id=resolved_investigator_id,
        max_turns=max_turns,
        evaluation_state=evaluation_state,
        initial_narrative_work_note_value=narrative_work_note_value,
        initial_keeper_turn_note_value=keeper_turn_note_value,
        initial_visible_turn_note_value=visible_turn_note_value,
    )
    run_result = _finalize_experimental_one_shot_run_result_internal_tooling(
        snapshot=snapshot,
        run_result=run_result,
    )
    ending_status_label = EXPERIMENTAL_ONE_SHOT_ENDING_STATUS_LABELS.get(
        run_result.ending_status,
        run_result.ending_status,
    )
    notice_parts = [
        _normalize_form_text(notice_prefix) or "",
        (
            f"bounded autopilot run 已结束：{ending_status_label}。"
            "当前只保留 run-local transcript / ending summary，不会写入 authoritative state。"
        ),
    ]
    notice = " ".join(part for part in notice_parts if part)
    return _render_app_experimental_ai_demo_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
        selected_investigator_id=resolved_investigator_id,
        last_run_recall=last_run_recall,
        kp_result=run_result.kp_result,
        investigator_result=run_result.investigator_result,
        current_turn_index=run_result.current_turn_index,
        kp_turn_bridge=run_result.kp_turn_bridge,
        investigator_turn_bridge=run_result.investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=run_result.narrative_work_note_value,
        keeper_turn_note_value=run_result.keeper_turn_note_value,
        visible_turn_note_value=run_result.visible_turn_note_value,
        keeper_draft_applied=run_result.keeper_draft_applied,
        visible_draft_applied=run_result.visible_draft_applied,
        orchestration_preview_html=_render_experimental_ai_demo_preview_chain(
            session_id=session_id,
            current_turn_index=run_result.current_turn_index,
            kp_result=run_result.kp_result,
            investigator_result=run_result.investigator_result,
            keeper_draft_result=run_result.keeper_draft_result,
            visible_draft_result=run_result.visible_draft_result,
        ),
        one_shot_max_turns=max_turns,
        one_shot_run_result=run_result,
        one_shot_run_html=_render_experimental_one_shot_run_panel(
            run_result=run_result,
            last_run_recall=last_run_recall,
        ),
        demo_boot=demo_boot,
        notice=notice,
    )


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
    demo_boot: str | None = None,
    last_run_status: str | None = None,
    last_run_reason: str | None = None,
    last_run_provider: str | None = None,
    last_run_model: str | None = None,
) -> HTMLResponse:
    demo_boot_enabled = _is_demo_boot_enabled(demo_boot)
    normalized_group = _normalize_form_text(playtest_group)
    last_run_recall = _read_experimental_autopilot_last_run_recall(
        {
            "last_run_status": last_run_status,
            "last_run_reason": last_run_reason,
            "last_run_provider": last_run_provider,
            "last_run_model": last_run_model,
        }
    )
    form_values = (
        _build_demo_boot_setup_form_values(playtest_group=normalized_group)
        if demo_boot_enabled
        else _default_playtest_setup_form_values()
    )
    if normalized_group:
        form_values["playtest_group"] = normalized_group
    return _render_setup_page(
        form_values=form_values,
        demo_boot=demo_boot_enabled,
        last_run_recall=last_run_recall,
    )


@router.post("/setup", response_class=HTMLResponse)
async def web_app_create_session(
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    raw_form = await _read_form_payload(request)
    demo_boot_enabled = _is_demo_boot_enabled(raw_form.get("demo_boot"))
    launch_target = _normalize_form_text(raw_form.get("launch_target")) or ""
    autorun_one_shot = _is_demo_boot_enabled(raw_form.get("autorun_one_shot"))
    last_run_recall = _read_experimental_autopilot_last_run_recall(raw_form)
    form = _normalize_playtest_setup_form_values(raw_form)
    try:
        start_request = _build_playtest_setup_request(form)
        response = service.start_session(start_request)
        if demo_boot_enabled and launch_target == "experimental_ai_demo":
            if autorun_one_shot:
                return _render_experimental_ai_demo_one_shot_run_from_service(
                    service=service,
                    local_llm_service=local_llm_service,
                    session_id=response.session_id,
                    max_turns=EXPERIMENTAL_ONE_SHOT_DEFAULT_MAX_TURNS,
                    last_run_recall=last_run_recall,
                    demo_boot=True,
                    notice_prefix=(
                        "launcher / demo boot 已创建 sample session，并直接复用现有 bounded autoplay observer 主链。"
                    ),
                )
            return RedirectResponse(
                url=_experimental_ai_demo_session_boot_href_with_recall(
                    response.session_id,
                    recall=last_run_recall,
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return RedirectResponse(
            url=f"/app/sessions/{response.session_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValidationError, ValueError) as exc:
        return _render_setup_page(
            form_values=form,
            demo_boot=demo_boot_enabled,
            last_run_recall=last_run_recall,
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


@router.get("/experimental-ai-demo", include_in_schema=False)
def web_app_experimental_ai_demo_launcher_entry(
    demo_boot: str | None = None,
    fresh: str | None = None,
    last_run_status: str | None = None,
    last_run_reason: str | None = None,
    last_run_provider: str | None = None,
    last_run_model: str | None = None,
    service: SessionService = Depends(get_session_service),
) -> RedirectResponse:
    demo_boot_enabled = _is_demo_boot_enabled(demo_boot)
    fresh_requested = _is_demo_boot_enabled(fresh)
    last_run_recall = _read_experimental_autopilot_last_run_recall(
        {
            "last_run_status": last_run_status,
            "last_run_reason": last_run_reason,
            "last_run_provider": last_run_provider,
            "last_run_model": last_run_model,
        }
    )
    sessions = [session.model_dump(mode="json") for session in service.list_sessions()]
    if demo_boot_enabled:
        if not fresh_requested:
            recent_demo_session_id = _resolve_recent_demo_boot_session_id(sessions)
            if recent_demo_session_id:
                return RedirectResponse(
                    url=_experimental_ai_demo_session_boot_href_with_recall(
                        recent_demo_session_id,
                        recall=last_run_recall,
                    ),
                    status_code=status.HTTP_303_SEE_OTHER,
                )
        return RedirectResponse(
            url=_experimental_ai_demo_setup_boot_href_with_recall(
                fresh=fresh_requested,
                recall=last_run_recall,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not sessions:
        return RedirectResponse(url="/app/sessions", status_code=status.HTTP_303_SEE_OTHER)
    latest_session_id = _normalize_form_text(sessions[0].get("session_id")) or ""
    return RedirectResponse(
        url=f"/app/sessions/{latest_session_id}/experimental-ai-demo",
        status_code=status.HTTP_303_SEE_OTHER,
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


@router.get("/sessions/{session_id}/experimental-ai-demo", response_class=HTMLResponse)
def web_app_experimental_ai_demo(
    session_id: str,
    investigator_id: str | None = None,
    demo_boot: str | None = None,
    last_run_status: str | None = None,
    last_run_reason: str | None = None,
    last_run_provider: str | None = None,
    last_run_model: str | None = None,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    demo_boot_enabled = _is_demo_boot_enabled(demo_boot)
    last_run_recall = _read_experimental_autopilot_last_run_recall(
        {
            "last_run_status": last_run_status,
            "last_run_reason": last_run_reason,
            "last_run_provider": last_run_provider,
            "last_run_model": last_run_model,
        }
    )
    return _render_app_experimental_ai_demo_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
        selected_investigator_id=_normalize_form_text(investigator_id),
        last_run_recall=last_run_recall,
        demo_boot=demo_boot_enabled,
        notice=(
            "launcher / demo boot 已就绪：当前页默认用于 observer autoplay demo。"
            "已自动选中首个 investigator，并保留 bounded one-click 参数；点击一次“一键开始 bounded autopilot run”即可观察过程与结果。"
            if demo_boot_enabled
            else None
        ),
    )


@router.post("/sessions/{session_id}/experimental-ai-demo/run", response_class=HTMLResponse)
async def web_app_experimental_ai_demo_run(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    selected_investigator_id = _normalize_form_text(form.get("investigator_id"))
    previous_turn_index = _parse_turn_index(form.get("current_turn_index"))
    evaluation_state = _normalize_experimental_demo_rubric_state(form)
    previous_kp_payload = _turn_bridge_payload_from_form(form, prefix="previous_kp")
    previous_investigator_payload = _turn_bridge_payload_from_form(
        form,
        prefix="previous_investigator",
    )
    narrative_work_note_value = _normalize_form_text(form.get("narrative_work_note")) or ""
    keeper_turn_outcome_note = _normalize_form_text(form.get("keeper_turn_outcome_note")) or ""
    visible_turn_outcome_note = _normalize_form_text(form.get("visible_turn_outcome_note")) or ""
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Experimental AI Demo",
            title="Experimental AI Demo 不可用",
            summary="当前无法加载实验 harness 所需的 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Experimental AI Demo Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    candidates = _demo_investigator_candidates(
        participants,
        keeper_id=snapshot.get("keeper_id"),
    )
    candidate_ids = {
        str(item.get("actor_id") or "")
        for item in candidates
        if str(item.get("actor_id") or "")
    }
    resolved_investigator_id = selected_investigator_id
    if not resolved_investigator_id and candidates:
        resolved_investigator_id = str(candidates[0].get("actor_id") or "")
    if not resolved_investigator_id or resolved_investigator_id not in candidate_ids:
        return _render_app_experimental_ai_demo_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            selected_investigator_id=resolved_investigator_id,
            narrative_work_note_value=narrative_work_note_value,
            detail="当前无法建立可用的 investigator visible summary；请先选择一个有效调查员视角。",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    investigator_view = service.get_session_view(
        session_id,
        viewer_id=resolved_investigator_id,
        viewer_role=ViewerRole.INVESTIGATOR,
    ).model_dump(mode="json")
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
    ).model_dump(mode="json")
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
    )
    kp_turn_bridge = _build_experimental_ai_kp_turn_bridge(
        previous_turn_index=previous_turn_index,
        prior_kp_payload=previous_kp_payload,
        prior_investigator_payload=previous_investigator_payload,
        keeper_turn_note=keeper_turn_outcome_note,
        visible_turn_note=visible_turn_outcome_note,
    )
    investigator_turn_bridge = _build_experimental_ai_investigator_turn_bridge(
        previous_turn_index=previous_turn_index,
        prior_investigator_payload=previous_investigator_payload,
        visible_turn_note=visible_turn_outcome_note,
    )
    next_turn_index = previous_turn_index + 1
    kp_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_kp_demo",
        task_key="demo_loop",
        task_label=EXPERIMENTAL_AI_KP_DEMO_TASKS["demo_loop"],
        context=_build_experimental_ai_kp_demo_context(
            snapshot=snapshot,
            context_pack=context_pack,
            compressed_context=compressed_context,
            turn_bridge=kp_turn_bridge,
        ),
    )
    investigator_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_investigator_demo",
        task_key="demo_loop",
        task_label=EXPERIMENTAL_AI_INVESTIGATOR_DEMO_TASKS["demo_loop"],
        context=_build_experimental_ai_investigator_demo_context(
            viewer_id=resolved_investigator_id,
            view=investigator_view,
            turn_bridge=investigator_turn_bridge,
        ),
    )
    continuation_notice = (
        f"已生成第 {next_turn_index} 轮 isolated experimental AI demo 输出；本轮已参考上一轮页内 continuity bridge，仍不会自动写入主状态。"
        if kp_turn_bridge or investigator_turn_bridge
        else f"已生成第 {next_turn_index} 轮 isolated experimental AI demo 输出；仅供观察 narrative loop，不会自动写入主状态。"
    )
    return _render_experimental_ai_demo_page(
        session_id=session_id,
        snapshot=snapshot,
        compressed_context=compressed_context,
        investigator_candidates=candidates,
        selected_investigator_id=resolved_investigator_id,
        selected_investigator_view=investigator_view,
        kp_result=kp_result,
        investigator_result=investigator_result,
        current_turn_index=next_turn_index,
        kp_turn_bridge=kp_turn_bridge,
        investigator_turn_bridge=investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        notice=continuation_notice,
    )


@router.post("/sessions/{session_id}/experimental-ai-demo/self-play-preview", response_class=HTMLResponse)
async def web_app_experimental_ai_demo_self_play_preview(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    selected_investigator_id = _normalize_form_text(form.get("investigator_id"))
    previous_turn_index = _parse_turn_index(form.get("current_turn_index"))
    evaluation_state = _normalize_experimental_demo_rubric_state(form)
    previous_kp_payload = _turn_bridge_payload_from_form(form, prefix="previous_kp")
    previous_investigator_payload = _turn_bridge_payload_from_form(
        form,
        prefix="previous_investigator",
    )
    narrative_work_note_value = _normalize_form_text(form.get("narrative_work_note")) or ""
    existing_keeper_turn_note = (
        _normalize_form_text(form.get("keeper_turn_outcome_note")) or ""
    )
    existing_visible_turn_note = (
        _normalize_form_text(form.get("visible_turn_outcome_note")) or ""
    )
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Experimental AI Demo",
            title="Experimental AI Demo 不可用",
            summary="当前无法加载实验 harness 所需的 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Experimental AI Demo Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    candidates = _demo_investigator_candidates(
        participants,
        keeper_id=snapshot.get("keeper_id"),
    )
    candidate_ids = {
        str(item.get("actor_id") or "")
        for item in candidates
        if str(item.get("actor_id") or "")
    }
    resolved_investigator_id = selected_investigator_id
    if not resolved_investigator_id and candidates:
        resolved_investigator_id = str(candidates[0].get("actor_id") or "")
    if not resolved_investigator_id or resolved_investigator_id not in candidate_ids:
        return _render_app_experimental_ai_demo_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            selected_investigator_id=resolved_investigator_id,
            narrative_work_note_value=narrative_work_note_value,
            detail="当前无法建立可用的 investigator visible summary；请先选择一个有效调查员视角。",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    investigator_view = service.get_session_view(
        session_id,
        viewer_id=resolved_investigator_id,
        viewer_role=ViewerRole.INVESTIGATOR,
    ).model_dump(mode="json")
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
    ).model_dump(mode="json")
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
    )
    kp_turn_bridge = _build_experimental_ai_kp_turn_bridge(
        previous_turn_index=previous_turn_index,
        prior_kp_payload=previous_kp_payload,
        prior_investigator_payload=previous_investigator_payload,
        keeper_turn_note=existing_keeper_turn_note,
        visible_turn_note=existing_visible_turn_note,
    )
    investigator_turn_bridge = _build_experimental_ai_investigator_turn_bridge(
        previous_turn_index=previous_turn_index,
        prior_investigator_payload=previous_investigator_payload,
        visible_turn_note=existing_visible_turn_note,
    )
    next_turn_index = previous_turn_index + 1
    kp_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_kp_demo",
        task_key="demo_loop",
        task_label=EXPERIMENTAL_AI_KP_DEMO_TASKS["demo_loop"],
        context=_build_experimental_ai_kp_demo_context(
            snapshot=snapshot,
            context_pack=context_pack,
            compressed_context=compressed_context,
            turn_bridge=kp_turn_bridge,
        ),
    )
    investigator_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_investigator_demo",
        task_key="demo_loop",
        task_label=EXPERIMENTAL_AI_INVESTIGATOR_DEMO_TASKS["demo_loop"],
        context=_build_experimental_ai_investigator_demo_context(
            viewer_id=resolved_investigator_id,
            view=investigator_view,
            turn_bridge=investigator_turn_bridge,
        ),
    )
    keeper_draft_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_keeper_continuity_draft",
        task_key="draft_bridge",
        task_label=EXPERIMENTAL_AI_KEEPER_CONTINUITY_DRAFT_TASKS["draft_bridge"],
        context=_build_experimental_keeper_continuity_draft_context(
            snapshot=snapshot,
            compressed_context=compressed_context,
            kp_result=kp_result,
            investigator_result=investigator_result,
            evaluation_state=evaluation_state,
        ),
    )
    visible_draft_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_visible_continuity_draft",
        task_key="draft_bridge",
        task_label=EXPERIMENTAL_AI_VISIBLE_CONTINUITY_DRAFT_TASKS["draft_bridge"],
        context=_build_experimental_visible_continuity_draft_context(
            viewer_id=resolved_investigator_id,
            view=investigator_view,
            investigator_result=investigator_result,
        ),
    )
    keeper_draft_text = _normalize_form_text(
        ((keeper_draft_result.assistant or None) and (keeper_draft_result.assistant or None).draft_text)
    ) or existing_keeper_turn_note
    visible_draft_text = _normalize_form_text(
        ((visible_draft_result.assistant or None) and (visible_draft_result.assistant or None).draft_text)
    ) or existing_visible_turn_note
    keeper_draft_applied = bool(
        keeper_draft_result.assistant
        and _normalize_form_text(keeper_draft_result.assistant.draft_text)
    )
    visible_draft_applied = bool(
        visible_draft_result.assistant
        and _normalize_form_text(visible_draft_result.assistant.draft_text)
    )
    notice = (
        f"已串行预演第 {next_turn_index} 轮 self-play preview chain，并将 dual continuity drafts 回填到当前页 textarea；仍需 Keeper 人工审阅，不会自动提交或继续下一轮。"
    )
    return _render_app_experimental_ai_demo_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
        selected_investigator_id=resolved_investigator_id,
        kp_result=kp_result,
        investigator_result=investigator_result,
        current_turn_index=next_turn_index,
        kp_turn_bridge=kp_turn_bridge,
        investigator_turn_bridge=investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_draft_text,
        visible_turn_note_value=visible_draft_text,
        keeper_draft_applied=keeper_draft_applied,
        visible_draft_applied=visible_draft_applied,
        orchestration_preview_html=_render_experimental_ai_demo_preview_chain(
            session_id=session_id,
            current_turn_index=next_turn_index,
            kp_result=kp_result,
            investigator_result=investigator_result,
            keeper_draft_result=keeper_draft_result,
            visible_draft_result=visible_draft_result,
        ),
        notice=notice,
    )


@router.post("/sessions/{session_id}/experimental-ai-demo/one-shot-run", response_class=HTMLResponse)
async def web_app_experimental_ai_demo_one_shot_run(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    return _render_experimental_ai_demo_one_shot_run_from_service(
        service=service,
        local_llm_service=local_llm_service,
        session_id=session_id,
        selected_investigator_id=_normalize_form_text(form.get("investigator_id")) or "",
        max_turns=_parse_one_shot_max_turns(form.get("max_turns")),
        last_run_recall=_read_experimental_autopilot_last_run_recall(form),
        evaluation_state=_normalize_experimental_demo_rubric_state(form),
        narrative_work_note_value=_normalize_form_text(form.get("seed_narrative_work_note"))
        or "",
        keeper_turn_note_value=(
            _normalize_form_text(form.get("seed_keeper_turn_outcome_note")) or ""
        ),
        visible_turn_note_value=(
            _normalize_form_text(form.get("seed_visible_turn_outcome_note")) or ""
        ),
        demo_boot=_is_demo_boot_enabled(form.get("demo_boot")),
    )


@router.post("/sessions/{session_id}/experimental-ai-demo/draft-continuity", response_class=HTMLResponse)
async def web_app_experimental_ai_demo_draft_continuity(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    selected_investigator_id = _normalize_form_text(form.get("investigator_id"))
    current_turn_index = _parse_turn_index(form.get("current_turn_index"))
    kp_result = _assistant_result_from_hidden_json(form.get("current_kp_result_json"))
    investigator_result = _assistant_result_from_hidden_json(
        form.get("current_investigator_result_json")
    )
    evaluation_state = _normalize_experimental_demo_rubric_state(form)
    narrative_work_note_value = _normalize_form_text(form.get("narrative_work_note")) or ""
    existing_keeper_turn_note = (
        _normalize_form_text(form.get("keeper_turn_outcome_note")) or ""
    )
    existing_visible_turn_note = (
        _normalize_form_text(form.get("visible_turn_outcome_note")) or ""
    )
    kp_turn_bridge = _build_experimental_kp_echo_bridge_from_flags(
        has_keeper_continuity=_form_flag_enabled(
            form.get("current_kp_has_keeper_continuity")
        ),
        has_visible_continuity=_form_flag_enabled(
            form.get("current_kp_has_visible_continuity")
        ),
    )
    investigator_turn_bridge = _build_experimental_investigator_echo_bridge_from_flags(
        has_visible_continuity=_form_flag_enabled(
            form.get("current_investigator_has_visible_continuity")
        ),
    )
    if kp_result is None or investigator_result is None:
        return _render_app_experimental_ai_demo_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            selected_investigator_id=selected_investigator_id,
            narrative_work_note_value=narrative_work_note_value,
            detail="当前无法恢复本轮 experimental demo 输出；请重新运行实验回合后再起草 continuity bridge。",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        session, keeper_view, _, _ = service.get_keeper_workspace(session_id)
    except LookupError as exc:
        body = _detail_block(extract_error_detail(exc)) + _page_head(
            eyebrow="Experimental AI Demo",
            title="Experimental AI Demo 不可用",
            summary="当前无法加载实验 harness 所需的 keeper workspace。",
            actions=[("返回 Sessions", "/app/sessions", "ghost")],
        )
        return render_web_app_shell(
            title=f"Session {session_id} Experimental AI Demo Missing",
            sidebar_html=_render_sidebar(active_section="keeper"),
            body_html=body,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    snapshot = session.model_dump(mode="json")
    participants = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
    ]
    candidates = _demo_investigator_candidates(
        participants,
        keeper_id=snapshot.get("keeper_id"),
    )
    candidate_ids = {
        str(item.get("actor_id") or "")
        for item in candidates
        if str(item.get("actor_id") or "")
    }
    resolved_investigator_id = selected_investigator_id
    if not resolved_investigator_id and candidates:
        resolved_investigator_id = str(candidates[0].get("actor_id") or "")
    if not resolved_investigator_id or resolved_investigator_id not in candidate_ids:
        return _render_app_experimental_ai_demo_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            selected_investigator_id=resolved_investigator_id,
            kp_result=kp_result,
            investigator_result=investigator_result,
            current_turn_index=current_turn_index,
            kp_turn_bridge=kp_turn_bridge,
            investigator_turn_bridge=investigator_turn_bridge,
            evaluation_state=evaluation_state,
            narrative_work_note_value=narrative_work_note_value,
            keeper_turn_note_value=existing_keeper_turn_note,
            visible_turn_note_value=existing_visible_turn_note,
            detail="当前无法建立可用的 investigator visible summary；请先选择一个有效调查员视角。",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    investigator_view = service.get_session_view(
        session_id,
        viewer_id=resolved_investigator_id,
        viewer_role=ViewerRole.INVESTIGATOR,
    ).model_dump(mode="json")
    context_pack = service.build_keeper_context_pack_from_workspace(
        session=session,
        keeper_view=keeper_view,
    ).model_dump(mode="json")
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
    )
    keeper_draft_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_keeper_continuity_draft",
        task_key="draft_bridge",
        task_label=EXPERIMENTAL_AI_KEEPER_CONTINUITY_DRAFT_TASKS["draft_bridge"],
        context=_build_experimental_keeper_continuity_draft_context(
            snapshot=snapshot,
            compressed_context=compressed_context,
            kp_result=kp_result,
            investigator_result=investigator_result,
            evaluation_state=evaluation_state,
        ),
    )
    visible_draft_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="experimental_ai_visible_continuity_draft",
        task_key="draft_bridge",
        task_label=EXPERIMENTAL_AI_VISIBLE_CONTINUITY_DRAFT_TASKS["draft_bridge"],
        context=_build_experimental_visible_continuity_draft_context(
            viewer_id=resolved_investigator_id,
            view=investigator_view,
            investigator_result=investigator_result,
        ),
    )
    keeper_draft_text = _normalize_form_text(
        ((keeper_draft_result.assistant or None) and (keeper_draft_result.assistant or None).draft_text)
    ) or existing_keeper_turn_note
    visible_draft_text = _normalize_form_text(
        ((visible_draft_result.assistant or None) and (visible_draft_result.assistant or None).draft_text)
    ) or existing_visible_turn_note
    keeper_draft_applied = bool(
        keeper_draft_result.assistant and _normalize_form_text(keeper_draft_result.assistant.draft_text)
    )
    visible_draft_applied = bool(
        visible_draft_result.assistant and _normalize_form_text(visible_draft_result.assistant.draft_text)
    )
    notice = (
        "已起草 continuity bridge 草稿并填入当前页 textarea；仍需 Keeper 人工审阅、修改并手工触发下一轮。"
        if keeper_draft_applied or visible_draft_applied
        else "当前未生成可用的 continuity bridge 草稿；主流程不受影响。"
    )
    return _render_app_experimental_ai_demo_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
        selected_investigator_id=resolved_investigator_id,
        kp_result=kp_result,
        investigator_result=investigator_result,
        current_turn_index=current_turn_index,
        kp_turn_bridge=kp_turn_bridge,
        investigator_turn_bridge=investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_draft_text,
        visible_turn_note_value=visible_draft_text,
        keeper_draft_applied=keeper_draft_applied,
        visible_draft_applied=visible_draft_applied,
        notice=notice,
    )


@router.post("/sessions/{session_id}/experimental-ai-demo/evaluate", response_class=HTMLResponse)
async def web_app_experimental_ai_demo_evaluate(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    local_llm_service: LocalLLMService = Depends(get_local_llm_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    selected_investigator_id = _normalize_form_text(form.get("investigator_id"))
    current_turn_index = _parse_turn_index(form.get("current_turn_index"))
    kp_result = _assistant_result_from_hidden_json(form.get("current_kp_result_json"))
    investigator_result = _assistant_result_from_hidden_json(
        form.get("current_investigator_result_json")
    )
    evaluation_state = _normalize_experimental_demo_rubric_state(form)
    narrative_work_note_value = (
        _normalize_form_text(form.get("current_narrative_work_note")) or ""
    )
    keeper_turn_note_value = (
        _normalize_form_text(form.get("current_keeper_turn_outcome_note")) or ""
    )
    visible_turn_note_value = (
        _normalize_form_text(form.get("current_visible_turn_outcome_note")) or ""
    )
    keeper_draft_applied = _form_flag_enabled(form.get("current_keeper_draft_applied"))
    visible_draft_applied = _form_flag_enabled(form.get("current_visible_draft_applied"))
    kp_turn_bridge = _build_experimental_kp_echo_bridge_from_flags(
        has_keeper_continuity=_form_flag_enabled(
            form.get("current_kp_has_keeper_continuity")
        ),
        has_visible_continuity=_form_flag_enabled(
            form.get("current_kp_has_visible_continuity")
        ),
    )
    investigator_turn_bridge = _build_experimental_investigator_echo_bridge_from_flags(
        has_visible_continuity=_form_flag_enabled(
            form.get("current_investigator_has_visible_continuity")
        ),
    )
    if kp_result is None or investigator_result is None:
        return _render_app_experimental_ai_demo_from_service(
            service=service,
            session_id=session_id,
            local_llm_service=local_llm_service,
            selected_investigator_id=selected_investigator_id,
            narrative_work_note_value=narrative_work_note_value,
            detail="当前无法恢复本轮 experimental demo 输出；请重新运行实验回合后再做页内评估。",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _render_app_experimental_ai_demo_from_service(
        service=service,
        session_id=session_id,
        local_llm_service=local_llm_service,
        selected_investigator_id=selected_investigator_id,
        kp_result=kp_result,
        investigator_result=investigator_result,
        current_turn_index=current_turn_index,
        kp_turn_bridge=kp_turn_bridge,
        investigator_turn_bridge=investigator_turn_bridge,
        evaluation_state=evaluation_state,
        narrative_work_note_value=narrative_work_note_value,
        keeper_turn_note_value=keeper_turn_note_value,
        visible_turn_note_value=visible_turn_note_value,
        keeper_draft_applied=keeper_draft_applied,
        visible_draft_applied=visible_draft_applied,
        notice="已记录当前页实验评估；仅用于当前页比较，不会写入主状态，也不会跨刷新保留。",
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
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
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
            compressed_context=compressed_context,
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
        compressed_context=compressed_context,
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
    compressed_context = _build_keeper_compressed_context_payload(
        service=service,
        context_pack=context_pack,
    )
    assistant_result = _generate_local_llm_assistant(
        local_llm_service=local_llm_service,
        workspace_key="session_recap",
        task_key=selected_task,
        task_label=task_label,
        context=_build_recap_assistant_context(
            snapshot=snapshot,
            context_pack=context_pack,
            compressed_context=compressed_context,
        ),
    )
    return _render_recap_page(
        session_id=session_id,
        snapshot=snapshot,
        context_pack=context_pack,
        compressed_context=compressed_context,
        assistant_result=assistant_result,
        selected_assistant_task=selected_task,
    )
