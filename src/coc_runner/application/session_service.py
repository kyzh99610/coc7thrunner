from __future__ import annotations

import re
from datetime import datetime, timezone
from random import randint
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from coc_runner.domain.errors import ConflictError
from coc_runner.domain.dice import roll_d100
from coc_runner.domain.models import (
    ActiveSceneObjective,
    ActionEffects,
    ActorType,
    CharacterImportFieldSource,
    CharacterImportSyncPolicy,
    CharacterImportSyncReport,
    ApplyCharacterImportRequest,
    ApplyCharacterImportResponse,
    AppliedEffectRecord,
    AuthoritativeAction,
    AuthoritativeActionSource,
    AuditActionType,
    AuditLogEntry,
    BeatCondition,
    BehaviorPrecedent,
    CharacterAttributes,
    CharacterStatEffect,
    ImportCharacterHookSeedRequest,
    ImportSceneHookSeedRequest,
    ClueStateEffect,
    ClueProgressState,
    CreateCheckpointRequest,
    CreateCheckpointResponse,
    DeleteCheckpointResponse,
    DraftAction,
    EffectContractOrigin,
    EventType,
    InventoryEffect,
    InvestigatorAttributeCheckRequest,
    InvestigatorAttributeCheckResponse,
    InvestigatorSanCheckRequest,
    InvestigatorSanCheckResponse,
    InvestigatorSkillCheckRequest,
    InvestigatorSkillCheckResponse,
    InvestigatorView,
    ImportCheckpointResponse,
    KPDraftRequest,
    KeeperLiveControlRequest,
    KeeperLiveControlResponse,
    ListCheckpointsResponse,
    KeeperPromptPriority,
    KeeperWorkflowState,
    KeeperPromptStatus,
    LanguagePreference,
    ManualActionRequest,
    CompletedObjectiveRecord,
    ObjectiveOrigin,
    ParticipantKind,
    PlayerActionRequest,
    PlayerActionResponse,
    ReviewDecision,
    ReviewDecisionType,
    ReviewDraftRequest,
    ReviewDraftResponse,
    ReviewStatus,
    ReviewedAction,
    RiskLevel,
    SanAftermathSuggestion,
    QueuedKPPrompt,
    RuleGroundingSummary,
    RollbackRequest,
    RollbackResponse,
    RestoreCheckpointRequest,
    RestoreCheckpointResponse,
    ScenarioBeat,
    ScenarioBeatStatus,
    ScenarioBeatTransitionRecord,
    ScenarioBeatTransitionType,
    ScenarioClue,
    ScenarioScene,
    ScenarioScaffold,
    ScenarioProgressState,
    SceneState,
    SceneTransitionEffect,
    SessionEvent,
    SessionCharacterState,
    SessionCheckpoint,
    SessionCheckpointExportPayload,
    SessionCheckpointSummary,
    SessionImportResponse,
    SessionImportWarning,
    SessionParticipant,
    SessionStatus,
    SessionStartRequest,
    SessionStartResponse,
    SessionState,
    SeedSuggestionHookRequest,
    SuggestionHookMaterial,
    StatusEffect,
    UpsertSuggestionHookRequest,
    UpdateKeeperPromptRequest,
    UpdateSessionLifecycleRequest,
    UpdateKeeperPromptResponse,
    UpdateCheckpointRequest,
    UpdateCheckpointResponse,
    VisibilityEffect,
    VisibilityEffectTarget,
    ViewerRole,
    VisibilityScope,
)
from coc_runner.error_details import (
    build_character_import_error_detail,
    build_session_action_error_detail,
    build_structured_error_detail,
    shape_validation_error_items,
)
from coc_runner.domain.secrets import filter_session_for_viewer, normalize_keeper_prompt_for_keeper
from coc_runner.infrastructure.knowledge_repositories import KnowledgeRepository
from coc_runner.infrastructure.repositories import SessionRepository
from knowledge.retrieval import KnowledgeRetriever
from knowledge.schemas import (
    CharacterImportReview,
    CharacterSheetExtraction,
    KnowledgeSourceState,
    RuleQueryResult,
)


_SAN_LOSS_EXPRESSION_PATTERN = re.compile(r"^(?:(\d+)|(\d+)d(\d+))$")


def _roll_san_loss_value(expression: str) -> int:
    normalized = expression.strip().lower()
    match = _SAN_LOSS_EXPRESSION_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"invalid SAN loss expression: {expression}")
    static_value, dice_count, dice_sides = match.groups()
    if static_value is not None:
        return int(static_value)
    count = int(dice_count or "0")
    sides = int(dice_sides or "0")
    return sum(randint(1, sides) for _ in range(count))
from knowledge.terminology import extract_term_matches, normalize_chinese_text


class SessionService:
    DEFAULT_KEEPER_ID = "keeper-1"
    MAX_REVIEW_VERSION_DRIFT = 2
    _RULES_QUERY_FALLBACK_SIGNALS = (
        "检定",
        "判定",
        "技能",
        "属性",
        "规则",
        "骰",
        "成功",
        "失败",
        "困难",
        "极难",
        "对抗",
        "奖励骰",
        "惩罚骰",
        "理智",
        "疯狂",
        "伤害",
        "护甲",
        "攻击",
        "闪避",
        "战斗",
        "san",
        "hp",
        "d100",
    )

    _RISK_PRIORITY = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    _SERVER_RISK_MARKERS = {
        "death": ("death", RiskLevel.CRITICAL),
        "死亡": ("death", RiskLevel.CRITICAL),
        "permanent_injury": ("permanent_injury", RiskLevel.HIGH),
        "permanent injury": ("permanent_injury", RiskLevel.HIGH),
        "永久伤害": ("permanent_injury", RiskLevel.HIGH),
        "永久伤残": ("permanent_injury", RiskLevel.HIGH),
        "san collapse": ("san_collapse", RiskLevel.HIGH),
        "san_collapse": ("san_collapse", RiskLevel.HIGH),
        "理智崩溃": ("san_collapse", RiskLevel.HIGH),
        "major san loss": ("major_san_loss", RiskLevel.HIGH),
        "major_san_loss": ("major_san_loss", RiskLevel.HIGH),
        "大量理智损失": ("major_san_loss", RiskLevel.HIGH),
        "secret reveal": ("secret_reveal", RiskLevel.HIGH),
        "secret_reveal": ("secret_reveal", RiskLevel.HIGH),
        "揭露秘密": ("secret_reveal", RiskLevel.HIGH),
        "秘密暴露": ("secret_reveal", RiskLevel.HIGH),
        "ending": ("ending", RiskLevel.CRITICAL),
        "结局": ("ending", RiskLevel.CRITICAL),
        "结束": ("ending", RiskLevel.CRITICAL),
        "scene transition": ("scene_transition", RiskLevel.HIGH),
        "scene_transition": ("scene_transition", RiskLevel.HIGH),
        "场景切换": ("scene_transition", RiskLevel.HIGH),
    }
    _STATIC_IMPORT_FIELD_SCOPE = (
        "participant.display_name",
        "participant.character.name",
        "participant.character.occupation",
        "participant.character.age",
        "participant.character.attributes",
        "participant.character.skills",
        "participant.character.notes",
        "participant.imported_character_source_id",
        "character_state.core_stat_baseline",
        "character_state.skill_baseline",
        "character_state.import_source_id",
        "character_state.import_template_profile",
        "character_state.import_manual_review_required",
    )
    _SESSION_AUTHORITATIVE_FIELD_SCOPE = (
        "character_state.current_hit_points",
        "character_state.current_magic_points",
        "character_state.current_sanity",
        "character_state.inventory",
        "character_state.status_effects",
        "character_state.temporary_conditions",
        "character_state.clue_ids",
        "character_state.private_notes",
        "character_state.secret_state_refs",
    )

    def __init__(
        self,
        repository: SessionRepository,
        *,
        knowledge_repository: KnowledgeRepository | None = None,
        default_language: LanguagePreference = LanguagePreference.ZH_CN,
        behavior_memory_limit: int = 5,
    ) -> None:
        self.repository = repository
        self.knowledge_repository = knowledge_repository
        self.default_language = default_language
        self.behavior_memory_limit = behavior_memory_limit

    def _build_initial_scene_state(
        self,
        scenario: ScenarioScaffold,
        *,
        language: LanguagePreference,
    ) -> SceneState:
        if not scenario.scenes:
            return SceneState.model_validate(
                {
                    "title": self._message("opening_scene_title", language),
                    "summary": scenario.hook,
                    "phase": "setup",
                }
            )
        if scenario.start_scene_id is not None:
            start_scene = self._find_scenario_scene(
                scenario.scenes,
                scene_id=scenario.start_scene_id,
                scene_title=None,
            )
        else:
            start_scene = next((scene for scene in scenario.scenes if scene.revealed), scenario.scenes[0])
        return SceneState(
            scene_id=start_scene.scene_id,
            title=start_scene.title,
            summary=start_scene.summary,
            phase=start_scene.phase,
        )

    def _initialize_scenario_runtime_state(
        self,
        session: SessionState,
        *,
        current_time: datetime,
        language: LanguagePreference,
    ) -> None:
        for npc in session.scenario.npcs:
            session.progress_state.npc_attitudes.setdefault(npc.npc_id, npc.initial_attitude)
        if session.scenario.scenes:
            scene = self._find_scenario_scene(
                session.scenario.scenes,
                scene_id=session.current_scene.scene_id,
                scene_title=session.current_scene.title,
            )
            if scene is not None:
                self._reveal_scene_registry_entry(
                    session=session,
                    scene=scene,
                    source_action_id=None,
                    trigger_reason=self._message("scene_revealed_initial", language),
                    current_time=current_time,
                )
        self._sync_beat_statuses(session)
        if session.progress_state.current_beat is not None:
            current_beat = self._find_beat(session.scenario.beats, session.progress_state.current_beat)
            self._register_beat_objective(
                session=session,
                beat=current_beat,
                source_action_id=None,
                trigger_reason=self._message("beat_reason_current_selected", language),
            )

    def start_session(self, request: SessionStartRequest) -> SessionStartResponse:
        current_time = datetime.now(timezone.utc)
        session_language = self._resolve_language(request.language_preference)
        scenario = request.scenario.model_copy(
            update={"language_preference": request.scenario.language_preference or session_language}
        )
        error_context = {
            "scenario_id": scenario.scenario_id,
            "participant_count": len(request.participants),
        }
        current_scene = self._build_initial_scene_state(
            scenario,
            language=session_language,
        )
        try:
            session = SessionState(
                keeper_id=request.keeper_id or self.DEFAULT_KEEPER_ID,
                keeper_name=request.keeper_name,
                playtest_group=request.playtest_group,
                language_preference=session_language,
                allow_test_mode_self_review=request.allow_test_mode_self_review,
                scenario=scenario,
                current_scene=current_scene,
                participants=request.participants,
                character_states=self._build_initial_character_states(
                    request.participants,
                    current_time=current_time,
                ),
                progress_state=self._build_initial_progress_state(
                    scenario,
                    current_time=current_time,
                ),
                timeline=[
                    SessionEvent(
                        event_type=EventType.SESSION_STARTED,
                        actor_type=ActorType.SYSTEM,
                        visibility_scope=VisibilityScope.PUBLIC,
                        text=self._message("session_created_detail", session_language, title=scenario.title),
                        structured_payload={
                            "scenario_id": scenario.scenario_id,
                            "participant_count": len(request.participants),
                        },
                        language_preference=session_language,
                        created_at=current_time,
                    )
                ],
                created_at=current_time,
                updated_at=current_time,
            )
        except ValidationError as exc:
            raise ValueError(
                build_structured_error_detail(
                    code="session_start_invalid",
                    message=self._message("session_start_invalid", session_language),
                    scope="session_start_payload",
                    errors=shape_validation_error_items(exc.errors(include_input=False)),
                    **error_context,
                )
            ) from exc
        self._initialize_scenario_runtime_state(
            session,
            current_time=current_time,
            language=session_language,
        )
        self._initialize_imported_characters(
            session,
            current_time=current_time,
            language=session_language,
        )
        self._sync_beat_statuses(session)
        self.repository.create(session, reason="session_started")
        keeper_view = filter_session_for_viewer(
            session, viewer_id=None, viewer_role=ViewerRole.KEEPER
        )
        return SessionStartResponse(
            message=self._message("session_created", session_language),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=session_language,
            keeper_view=keeper_view,
        )

    def get_session_view(
        self,
        session_id: str,
        *,
        viewer_id: str | None,
        viewer_role: ViewerRole,
        language_preference: LanguagePreference | None = None,
    ) -> InvestigatorView:
        error_language = self._resolve_language(language_preference)
        error_context = {
            "session_id": session_id,
            "viewer_id": viewer_id,
            "viewer_role": viewer_role.value,
        }
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_state_session_not_found",
                    message=message,
                    scope="session_state_session",
                    **error_context,
                )
            ) from exc
        try:
            self._validate_viewer(
                session,
                viewer_id=viewer_id,
                viewer_role=viewer_role,
                language=error_language,
            )
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "viewer_id_required" if viewer_id is None else "viewer_not_participant",
                error_language,
                viewer_id=viewer_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="session_state_invalid",
                    message=message,
                    scope="session_state_request",
                    **error_context,
                )
            ) from exc
        return filter_session_for_viewer(
            session, viewer_id=viewer_id, viewer_role=viewer_role
        )

    def export_session(
        self,
        session_id: str,
        *,
        language_preference: LanguagePreference | None = None,
    ) -> dict[str, Any]:
        error_language = self._resolve_language(language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_export_session_not_found",
                    message=message,
                    scope="session_export_session",
                    session_id=session_id,
                )
            ) from exc
        keeper_view = filter_session_for_viewer(
            session,
            viewer_id=None,
            viewer_role=ViewerRole.KEEPER,
        )
        return keeper_view.model_dump(mode="json")

    def snapshot_session(
        self,
        session_id: str,
        *,
        language_preference: LanguagePreference | None = None,
    ) -> dict[str, Any]:
        error_language = self._resolve_language(language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_snapshot_session_not_found",
                    message=message,
                    scope="session_snapshot_session",
                    session_id=session_id,
                )
            ) from exc
        return session.model_dump(mode="json")

    def list_sessions(self) -> list[SessionState]:
        return self.repository.list_sessions()

    def get_keeper_workspace(
        self,
        session_id: str,
        *,
        language_preference: LanguagePreference | None = None,
    ) -> tuple[
        SessionState,
        InvestigatorView,
        list[SessionCheckpointSummary],
        list[SessionImportWarning],
    ]:
        error_language = self._resolve_language(language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_state_session_not_found",
                    message=message,
                    scope="session_state_session",
                    session_id=session_id,
                    viewer_role=ViewerRole.KEEPER.value,
                )
            ) from exc
        keeper_view = filter_session_for_viewer(
            session,
            viewer_id=None,
            viewer_role=ViewerRole.KEEPER,
        )
        checkpoints = self.repository.list_checkpoints(session.session_id)
        warnings = self._collect_import_warnings(session, language=error_language)
        return session, keeper_view, checkpoints, warnings

    def get_keeper_runtime_assistance(
        self,
        *,
        keeper_view: InvestigatorView,
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "rule_hints": self._collect_keeper_runtime_rule_hints(keeper_view),
            "knowledge_hints": self._collect_keeper_runtime_knowledge_hints(keeper_view),
        }

    def get_keeper_san_aftermath_suggestions(
        self,
        *,
        session: SessionState,
    ) -> dict[str, list[dict[str, Any]]]:
        suggestions_by_prompt_id: dict[str, list[dict[str, Any]]] = {}
        for prompt in session.progress_state.queued_kp_prompts:
            if prompt.category != "san_aftermath":
                continue
            suggestions = self._build_san_aftermath_suggestions(
                session=session,
                prompt=prompt,
            )
            if suggestions:
                suggestions_by_prompt_id[prompt.prompt_id] = [
                    suggestion.model_dump(mode="json") for suggestion in suggestions
                ]
        return suggestions_by_prompt_id

    def upsert_character_suggestion_hook(
        self,
        session_id: str,
        actor_id: str,
        request: UpsertSuggestionHookRequest,
    ) -> str:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="character_hook_session_not_found",
                    message=message,
                    scope="character_hook_session",
                    session_id=session_id,
                    actor_id=actor_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        participant = self._get_participant(session, actor_id, language=effective_language)
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="hook_material_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="hook_material_operator",
                session_id=session_id,
                operator_id=request.operator_id,
            ),
        )
        hook = self._upsert_suggestion_hook_material(
            participant.suggestion_hooks,
            hook_label=request.hook_label,
            hook_text=request.hook_text,
            current_time=current_time,
        )
        session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.HOOK_MATERIAL_UPDATED,
                actor_id=request.operator_id,
                subject_id=participant.actor_id,
                session_version=expected_version + 1,
                details={
                    "hook_scope": "character",
                    "hook_id": hook.hook_id,
                    "hook_label": hook.hook_label,
                },
                created_at=current_time,
            )
        )
        session.state_version += 1
        session.updated_at = current_time
        self._save_session(
            session,
            expected_version=expected_version,
            reason="character_hook_material_upserted",
            language=effective_language,
        )
        return self._message("character_hook_material_saved", effective_language)

    def upsert_scene_suggestion_hook(
        self,
        session_id: str,
        scene_id: str,
        request: UpsertSuggestionHookRequest,
    ) -> str:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="scene_hook_session_not_found",
                    message=message,
                    scope="scene_hook_session",
                    session_id=session_id,
                    scene_id=scene_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="hook_material_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="hook_material_operator",
                session_id=session_id,
                operator_id=request.operator_id,
            ),
        )
        scene = self._find_scenario_scene(
            session.scenario.scenes,
            scene_id=scene_id,
            scene_title=None,
        )
        if scene is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="scene_hook_not_found",
                    message=self._message("scene_not_found", effective_language, scene_id=scene_id),
                    scope="scene_hook_scene",
                    session_id=session_id,
                    scene_id=scene_id,
                )
            )
        hook = self._upsert_suggestion_hook_material(
            scene.suggestion_hooks,
            hook_label=request.hook_label,
            hook_text=request.hook_text,
            current_time=current_time,
        )
        session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.HOOK_MATERIAL_UPDATED,
                actor_id=request.operator_id,
                subject_id=scene.scene_id,
                session_version=expected_version + 1,
                details={
                    "hook_scope": "scene",
                    "hook_id": hook.hook_id,
                    "hook_label": hook.hook_label,
                },
                created_at=current_time,
            )
        )
        session.state_version += 1
        session.updated_at = current_time
        self._save_session(
            session,
            expected_version=expected_version,
            reason="scene_hook_material_upserted",
            language=effective_language,
        )
        return self._message("scene_hook_material_saved", effective_language)

    def seed_character_suggestion_hook(
        self,
        session_id: str,
        actor_id: str,
        request: SeedSuggestionHookRequest,
    ) -> str:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="character_hook_seed_session_not_found",
                    message=message,
                    scope="character_hook_seed_session",
                    session_id=session_id,
                    actor_id=actor_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        participant = self._get_participant(session, actor_id, language=effective_language)
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="hook_material_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="hook_material_operator",
                session_id=session_id,
                operator_id=request.operator_id,
            ),
        )
        seeded_hook = self._build_seeded_character_hook_material(
            participant=participant,
            current_time=current_time,
        )
        hook = self._upsert_suggestion_hook_material(
            participant.suggestion_hooks,
            hook_label=seeded_hook.hook_label,
            hook_text=seeded_hook.hook_text,
            current_time=current_time,
        )
        session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.HOOK_MATERIAL_UPDATED,
                actor_id=request.operator_id,
                subject_id=participant.actor_id,
                session_version=expected_version + 1,
                details={
                    "hook_scope": "character_seed",
                    "hook_id": hook.hook_id,
                    "hook_label": hook.hook_label,
                },
                created_at=current_time,
            )
        )
        session.state_version += 1
        session.updated_at = current_time
        self._save_session(
            session,
            expected_version=expected_version,
            reason="character_hook_material_seeded",
            language=effective_language,
        )
        return self._message("character_hook_material_seeded", effective_language)

    def seed_scene_suggestion_hook(
        self,
        session_id: str,
        scene_id: str,
        request: SeedSuggestionHookRequest,
    ) -> str:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="scene_hook_seed_session_not_found",
                    message=message,
                    scope="scene_hook_seed_session",
                    session_id=session_id,
                    scene_id=scene_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="hook_material_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="hook_material_operator",
                session_id=session_id,
                operator_id=request.operator_id,
            ),
        )
        scene = self._find_scenario_scene(
            session.scenario.scenes,
            scene_id=scene_id,
            scene_title=None,
        )
        if scene is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="scene_hook_not_found",
                    message=self._message("scene_not_found", effective_language, scene_id=scene_id),
                    scope="scene_hook_scene",
                    session_id=session_id,
                    scene_id=scene_id,
                )
            )
        seeded_hook = self._build_seeded_scene_hook_material(scene=scene, current_time=current_time)
        hook = self._upsert_suggestion_hook_material(
            scene.suggestion_hooks,
            hook_label=seeded_hook.hook_label,
            hook_text=seeded_hook.hook_text,
            current_time=current_time,
        )
        session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.HOOK_MATERIAL_UPDATED,
                actor_id=request.operator_id,
                subject_id=scene.scene_id,
                session_version=expected_version + 1,
                details={
                    "hook_scope": "scene_seed",
                    "hook_id": hook.hook_id,
                    "hook_label": hook.hook_label,
                },
                created_at=current_time,
            )
        )
        session.state_version += 1
        session.updated_at = current_time
        self._save_session(
            session,
            expected_version=expected_version,
            reason="scene_hook_material_seeded",
            language=effective_language,
        )
        return self._message("scene_hook_material_seeded", effective_language)

    def import_character_suggestion_hook_seed(
        self,
        session_id: str,
        actor_id: str,
        request: ImportCharacterHookSeedRequest,
    ) -> str:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="character_hook_import_session_not_found",
                    message=message,
                    scope="character_hook_import_session",
                    session_id=session_id,
                    actor_id=actor_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        participant = self._get_participant(session, actor_id, language=effective_language)
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="hook_material_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="hook_material_operator",
                session_id=session_id,
                operator_id=request.operator_id,
            ),
        )
        seeded_hook = self._build_imported_character_hook_material(
            occupation=request.occupation,
            notes=request.notes,
            seed_hint=request.seed_hint,
            current_time=current_time,
        )
        hook = self._upsert_suggestion_hook_material(
            participant.suggestion_hooks,
            hook_label=seeded_hook.hook_label,
            hook_text=seeded_hook.hook_text,
            current_time=current_time,
        )
        session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.HOOK_MATERIAL_UPDATED,
                actor_id=request.operator_id,
                subject_id=participant.actor_id,
                session_version=expected_version + 1,
                details={
                    "hook_scope": "character_import",
                    "hook_id": hook.hook_id,
                    "hook_label": hook.hook_label,
                },
                created_at=current_time,
            )
        )
        session.state_version += 1
        session.updated_at = current_time
        self._save_session(
            session,
            expected_version=expected_version,
            reason="character_hook_material_imported",
            language=effective_language,
        )
        return self._message("character_hook_material_imported", effective_language)

    def import_scene_suggestion_hook_seed(
        self,
        session_id: str,
        scene_id: str,
        request: ImportSceneHookSeedRequest,
    ) -> str:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="scene_hook_import_session_not_found",
                    message=message,
                    scope="scene_hook_import_session",
                    session_id=session_id,
                    scene_id=scene_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="hook_material_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="hook_material_operator",
                session_id=session_id,
                operator_id=request.operator_id,
            ),
        )
        scene = self._find_scenario_scene(
            session.scenario.scenes,
            scene_id=scene_id,
            scene_title=None,
        )
        if scene is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="scene_hook_not_found",
                    message=self._message("scene_not_found", effective_language, scene_id=scene_id),
                    scope="scene_hook_scene",
                    session_id=session_id,
                    scene_id=scene_id,
                )
            )
        seeded_hook = self._build_imported_scene_hook_material(
            scene=scene,
            title=request.title,
            short_context=request.short_context,
            seed_hint=request.seed_hint,
            current_time=current_time,
        )
        hook = self._upsert_suggestion_hook_material(
            scene.suggestion_hooks,
            hook_label=seeded_hook.hook_label,
            hook_text=seeded_hook.hook_text,
            current_time=current_time,
        )
        session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.HOOK_MATERIAL_UPDATED,
                actor_id=request.operator_id,
                subject_id=scene.scene_id,
                session_version=expected_version + 1,
                details={
                    "hook_scope": "scene_import",
                    "hook_id": hook.hook_id,
                    "hook_label": hook.hook_label,
                },
                created_at=current_time,
            )
        )
        session.state_version += 1
        session.updated_at = current_time
        self._save_session(
            session,
            expected_version=expected_version,
            reason="scene_hook_material_imported",
            language=effective_language,
        )
        return self._message("scene_hook_material_imported", effective_language)

    @staticmethod
    def _upsert_suggestion_hook_material(
        hooks: list[SuggestionHookMaterial],
        *,
        hook_label: str,
        hook_text: str,
        current_time: datetime,
    ) -> SuggestionHookMaterial:
        for hook in hooks:
            if hook.hook_label == hook_label:
                hook.hook_text = hook_text
                hook.updated_at = current_time
                return hook
        hook = SuggestionHookMaterial(
            hook_label=hook_label,
            hook_text=hook_text,
            created_at=current_time,
            updated_at=current_time,
        )
        hooks.append(hook)
        return hook

    @staticmethod
    def _trim_seed_hook_text(*parts: str, max_length: int = 200) -> str:
        normalized_parts = [part.strip() for part in parts if part and part.strip()]
        if not normalized_parts:
            return ""
        text = " ".join(normalized_parts)
        if len(text) <= max_length:
            return text
        return text[: max_length - 1].rstrip() + "…"

    def _build_seeded_character_hook_material(
        self,
        *,
        participant: SessionParticipant,
        current_time: datetime,
    ) -> SuggestionHookMaterial:
        occupation = participant.character.occupation.strip()
        hook_label = f"职业钩子：{occupation}" if occupation else f"角色钩子：{participant.display_name}"
        note_text = (
            participant.character.notes.strip()
            if isinstance(participant.character.notes, str) and participant.character.notes.strip()
            else ""
        )
        hook_text = self._trim_seed_hook_text(
            f"{occupation}的职业视角会放大对异常线索与失序叙述的敏感度。" if occupation else "",
            note_text,
        )
        return SuggestionHookMaterial(
            hook_label=hook_label,
            hook_text=hook_text or f"{participant.display_name}会对异常刺激表现出更鲜明的个人反应。",
            created_at=current_time,
            updated_at=current_time,
        )

    def _build_seeded_scene_hook_material(
        self,
        *,
        scene: ScenarioScene,
        current_time: datetime,
    ) -> SuggestionHookMaterial:
        hook_label = f"场景钩子：{scene.title.strip()}"
        hook_text = self._trim_seed_hook_text(
            f"{scene.title.strip()}的压抑氛围会放大异常显现带来的不安。",
        )
        return SuggestionHookMaterial(
            hook_label=hook_label,
            hook_text=hook_text,
            created_at=current_time,
            updated_at=current_time,
        )

    def _build_imported_character_hook_material(
        self,
        *,
        occupation: str,
        notes: str | None,
        seed_hint: str | None,
        current_time: datetime,
    ) -> SuggestionHookMaterial:
        hook_label = seed_hint or f"职业钩子：{occupation}"
        hook_text = self._trim_seed_hook_text(
            f"{occupation}：{notes}" if notes else f"{occupation}的职业视角会放大对异常线索与失序叙述的敏感度。",
        )
        return SuggestionHookMaterial(
            hook_label=hook_label,
            hook_text=hook_text,
            created_at=current_time,
            updated_at=current_time,
        )

    def _build_imported_scene_hook_material(
        self,
        *,
        scene: ScenarioScene,
        title: str | None,
        short_context: str,
        seed_hint: str | None,
        current_time: datetime,
    ) -> SuggestionHookMaterial:
        resolved_title = title or scene.title.strip()
        hook_label = seed_hint or f"场景钩子：{resolved_title}"
        hook_text = self._trim_seed_hook_text(short_context)
        return SuggestionHookMaterial(
            hook_label=hook_label,
            hook_text=hook_text,
            created_at=current_time,
            updated_at=current_time,
        )

    def _build_san_aftermath_suggestions(
        self,
        *,
        session: SessionState,
        prompt: QueuedKPPrompt,
    ) -> list[SanAftermathSuggestion]:
        if (
            prompt.category != "san_aftermath"
            or not prompt.san_source_label
            or prompt.san_loss_applied is None
            or prompt.san_loss_applied <= 0
        ):
            return []

        loss_applied = int(prompt.san_loss_applied)
        participant = (
            self._find_participant(session, prompt.san_actor_id)
            if prompt.san_actor_id
            else None
        )
        scene = self._find_scenario_scene(
            session.scenario.scenes,
            scene_id=prompt.scene_id,
            scene_title=None,
        )
        occupation = (
            participant.character.occupation.strip()
            if participant is not None and participant.character.occupation
            else None
        )
        current_scene_title = (
            scene.title.strip()
            if scene is not None and scene.title
            else session.current_scene.title.strip()
        )
        current_beat_title: str | None = None
        if prompt.beat_id:
            current_beat = next(
                (beat for beat in session.scenario.beats if beat.beat_id == prompt.beat_id),
                None,
            )
            if current_beat is not None:
                current_beat_title = current_beat.title.strip()
        character_hook = (
            participant.suggestion_hooks[-1]
            if participant is not None and participant.suggestion_hooks
            else None
        )
        scene_hook = scene.suggestion_hooks[-1] if scene is not None and scene.suggestion_hooks else None

        suggestions: list[SanAftermathSuggestion] = [
            SanAftermathSuggestion(
                label="惊惧失措" if loss_applied >= 3 else "短暂惊惧",
                duration_rounds=max(1, min(loss_applied, 4)),
                reason=f"“{prompt.san_source_label}”直接造成了 {loss_applied} 点理智冲击。",
            )
        ]
        if character_hook is not None:
            suggestions.append(
                SanAftermathSuggestion(
                    label=character_hook.hook_label,
                    duration_rounds=max(1, min(loss_applied + 1, 4)),
                    reason=f"角色钩子：{character_hook.hook_text}",
                )
            )
        elif occupation:
            suggestions.append(
                SanAftermathSuggestion(
                    label="偏执警觉",
                    duration_rounds=max(1, min(loss_applied + 1, 4)),
                    reason=f"角色职业“{occupation}”可能会把这次冲击放大为过度警觉。",
                )
            )
        if scene_hook is not None:
            suggestions.append(
                SanAftermathSuggestion(
                    label=scene_hook.hook_label,
                    duration_rounds=max(1, min(loss_applied, 3)),
                    reason=f"场景钩子：{scene_hook.hook_text}",
                )
            )
        elif current_scene_title and current_beat_title:
            suggestions.append(
                SanAftermathSuggestion(
                    label="强迫性回避",
                    duration_rounds=max(1, min(loss_applied, 3)),
                    reason=(
                        f"当前场景“{current_scene_title}”与节点“{current_beat_title}”仍在持续施压。"
                    ),
                )
            )
        elif current_scene_title:
            suggestions.append(
                SanAftermathSuggestion(
                    label="强迫性回避",
                    duration_rounds=max(1, min(loss_applied, 3)),
                    reason=f"当前场景“{current_scene_title}”仍在持续施压。",
                )
            )
        elif current_beat_title:
            suggestions.append(
                SanAftermathSuggestion(
                    label="强迫性回避",
                    duration_rounds=max(1, min(loss_applied, 3)),
                    reason=f"当前节点“{current_beat_title}”仍在持续施压。",
                )
            )
        return suggestions[:3]

    @staticmethod
    def _collect_keeper_runtime_rule_hints(
        keeper_view: InvestigatorView,
    ) -> list[dict[str, Any]]:
        hints: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        grounding_sources: list[tuple[str, RuleGroundingSummary]] = []

        for event in reversed(keeper_view.visible_events[-8:]):
            if event.rules_grounding is not None:
                grounding_sources.append(("最近行动", event.rules_grounding))
        for reviewed in reversed(keeper_view.visible_reviewed_actions[-4:]):
            if reviewed.rules_grounding is not None:
                grounding_sources.append(("已审草稿", reviewed.rules_grounding))
        for action in reversed(keeper_view.visible_authoritative_actions[-4:]):
            if action.rules_grounding is not None:
                grounding_sources.append(("权威结果", action.rules_grounding))

        for context_label, grounding in grounding_sources:
            summary = (
                grounding.review_summary
                or grounding.chinese_answer_draft
                or "未命中可用规则依据。"
            )
            key = (grounding.query_text, summary)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            hints.append(
                {
                    "title": grounding.query_text,
                    "summary": summary,
                    "citations": list(grounding.citations[:2]),
                    "context_label": context_label,
                }
            )
            if len(hints) >= 3:
                break
        return hints

    def _collect_keeper_runtime_knowledge_hints(
        self,
        keeper_view: InvestigatorView,
    ) -> list[dict[str, Any]]:
        if self.knowledge_repository is None:
            return []
        persisted_chunks = self.knowledge_repository.list_chunks()
        if not persisted_chunks:
            return []

        retriever = KnowledgeRetriever(persisted_chunks)
        hints: list[dict[str, Any]] = []
        seen_chunk_ids: set[str] = set()

        for query_text in self._build_keeper_runtime_query_texts(keeper_view):
            normalized_query = normalize_chinese_text(query_text)
            term_matches = extract_term_matches(query_text)
            matching_chunks = [
                chunk
                for chunk in persisted_chunks
                if not chunk.is_authoritative
                and retriever._matches_query(
                    chunk,
                    query_text,
                    normalized_query,
                    term_matches,
                )
            ]
            matching_chunks.sort(
                key=lambda chunk: (
                    chunk.priority,
                    retriever._chunk_relevance_score(
                        chunk,
                        normalized_query=normalized_query,
                        term_matches=term_matches,
                    ),
                ),
                reverse=True,
            )
            for raw_chunk in matching_chunks:
                if raw_chunk.chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(raw_chunk.chunk_id)
                hints.append(
                    {
                        "title": raw_chunk.title_zh,
                        "summary": raw_chunk.content,
                        "source_title": raw_chunk.source_title_zh
                        or raw_chunk.document_identity,
                        "query_text": query_text,
                        "citation": retriever._format_citation(raw_chunk),
                    }
                )
                if len(hints) >= 3:
                    return hints
        return hints

    @staticmethod
    def _build_keeper_runtime_query_texts(
        keeper_view: InvestigatorView,
    ) -> list[str]:
        query_texts: list[str] = []

        def append_query(candidate: str | None) -> None:
            if candidate is None:
                return
            normalized = candidate.strip()
            if len(normalized) < 2 or normalized in query_texts:
                return
            query_texts.append(normalized)

        append_query(keeper_view.current_scene.title)
        append_query(keeper_view.current_scene.summary)

        if keeper_view.progress_state is not None and keeper_view.progress_state.current_beat:
            current_beat = next(
                (
                    beat
                    for beat in keeper_view.scenario.beats
                    if beat.beat_id == keeper_view.progress_state.current_beat
                ),
                None,
            )
            if current_beat is not None:
                append_query(current_beat.title)
                append_query(current_beat.scene_objective)

        if keeper_view.keeper_workflow is not None:
            for objective in keeper_view.keeper_workflow.unresolved_objectives[:2]:
                append_query(objective.text)

        for event in reversed(keeper_view.visible_events[-2:]):
            append_query(event.text)

        return query_texts[:6]

    def _resolve_checkpoint_namespace_session_id(
        self,
        session_id: str,
        *,
        language: LanguagePreference,
        operator_id: str | None = None,
    ) -> str:
        try:
            session = self._load_session(session_id, language=language)
        except LookupError as exc:
            if self.repository.has_checkpoints_for_session(session_id):
                return session_id
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_checkpoint_session_not_found",
                    message=message,
                    scope="session_checkpoint_session",
                    session_id=session_id,
                    operator_id=operator_id,
                )
            ) from exc
        return session.session_id

    @staticmethod
    def _extract_checkpoint_import_hints(
        payload: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        checkpoint_payload = payload.get("checkpoint")
        if not isinstance(checkpoint_payload, dict):
            return None, None
        source_session_id = (
            checkpoint_payload.get("source_session_id")
            if isinstance(checkpoint_payload.get("source_session_id"), str)
            else None
        )
        original_checkpoint_id = (
            checkpoint_payload.get("checkpoint_id")
            if isinstance(checkpoint_payload.get("checkpoint_id"), str)
            else None
        )
        return source_session_id, original_checkpoint_id

    def create_checkpoint(
        self,
        session_id: str,
        request: CreateCheckpointRequest,
    ) -> CreateCheckpointResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_checkpoint_session_not_found",
                    message=message,
                    scope="session_checkpoint_session",
                    session_id=session_id,
                    operator_id=request.operator_id,
                )
            ) from exc

        checkpoint = SessionCheckpoint(
            checkpoint_id=f"checkpoint-{uuid4().hex}",
            source_session_id=session.session_id,
            source_session_version=session.state_version,
            label=request.label,
            note=request.note,
            created_by=request.operator_id,
            created_at=datetime.now(timezone.utc),
            snapshot_payload=session.model_dump(mode="json"),
        )
        self.repository.create_checkpoint(checkpoint)
        return CreateCheckpointResponse(
            message=self._message("checkpoint_created", error_language),
            session_id=session.session_id,
            checkpoint=SessionCheckpointSummary.model_validate(
                checkpoint.model_dump(exclude={"snapshot_payload"})
            ),
        )

    def list_checkpoints(
        self,
        session_id: str,
        *,
        language_preference: LanguagePreference | None = None,
    ) -> ListCheckpointsResponse:
        error_language = self._resolve_language(language_preference)
        namespace_session_id = self._resolve_checkpoint_namespace_session_id(
            session_id,
            language=error_language,
        )
        return ListCheckpointsResponse(
            session_id=namespace_session_id,
            checkpoints=self.repository.list_checkpoints(namespace_session_id),
        )

    def export_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        language_preference: LanguagePreference | None = None,
    ) -> SessionCheckpointExportPayload:
        error_language = self._resolve_language(language_preference)
        namespace_session_id = self._resolve_checkpoint_namespace_session_id(
            session_id,
            language=error_language,
        )
        checkpoint = self.repository.get_checkpoint(namespace_session_id, checkpoint_id)
        if checkpoint is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="session_checkpoint_not_found",
                    message=self._message(
                        "checkpoint_not_found",
                        error_language,
                        checkpoint_id=checkpoint_id,
                    ),
                    scope="session_checkpoint_record",
                    session_id=namespace_session_id,
                    checkpoint_id=checkpoint_id,
                    source_session_id=namespace_session_id,
                )
            )
        return SessionCheckpointExportPayload(
            exported_at=datetime.now(timezone.utc),
            checkpoint=checkpoint,
        )

    def restore_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        request: RestoreCheckpointRequest,
    ) -> RestoreCheckpointResponse:
        error_language = self._resolve_language(request.language_preference)
        checkpoint = self.repository.get_checkpoint(session_id, checkpoint_id)
        if checkpoint is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="session_checkpoint_not_found",
                    message=self._message(
                        "checkpoint_not_found",
                        error_language,
                        checkpoint_id=checkpoint_id,
                    ),
                    scope="session_checkpoint_record",
                    session_id=session_id,
                    checkpoint_id=checkpoint_id,
                    source_session_id=session_id,
                )
            )
        import_response = self.import_session(
            checkpoint.snapshot_payload,
            language_preference=request.language_preference,
        )
        return RestoreCheckpointResponse(
            checkpoint_id=checkpoint.checkpoint_id,
            source_session_id=checkpoint.source_session_id,
            new_session_id=import_response.new_session_id,
            state_version=import_response.state_version,
            warnings=import_response.warnings,
        )

    def update_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        request: UpdateCheckpointRequest,
    ) -> UpdateCheckpointResponse:
        error_language = self._resolve_language(request.language_preference)
        namespace_session_id = self._resolve_checkpoint_namespace_session_id(
            session_id,
            language=error_language,
            operator_id=request.operator_id,
        )

        checkpoint = self.repository.get_checkpoint(namespace_session_id, checkpoint_id)
        if checkpoint is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="session_checkpoint_not_found",
                    message=self._message(
                        "checkpoint_not_found",
                        error_language,
                        checkpoint_id=checkpoint_id,
                    ),
                    scope="session_checkpoint_record",
                    session_id=namespace_session_id,
                    checkpoint_id=checkpoint_id,
                    source_session_id=namespace_session_id,
                )
            )

        if not ({"label", "note"} & request.model_fields_set):
            raise ValueError(
                build_session_action_error_detail(
                    code="session_checkpoint_update_invalid",
                    message=self._message("checkpoint_update_invalid", error_language),
                    scope="session_checkpoint_update",
                    session_id=namespace_session_id,
                    checkpoint_id=checkpoint_id,
                    source_session_id=namespace_session_id,
                    operator_id=request.operator_id,
                )
            )

        if "label" in request.model_fields_set:
            checkpoint.label = request.label or checkpoint.label
        if "note" in request.model_fields_set:
            checkpoint.note = request.note

        self.repository.save_checkpoint_metadata(checkpoint)
        return UpdateCheckpointResponse(
            message=self._message("checkpoint_updated", error_language),
            session_id=namespace_session_id,
            checkpoint=SessionCheckpointSummary.model_validate(
                checkpoint.model_dump(exclude={"snapshot_payload"})
            ),
        )

    def delete_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        language_preference: LanguagePreference | None = None,
    ) -> DeleteCheckpointResponse:
        error_language = self._resolve_language(language_preference)
        namespace_session_id = self._resolve_checkpoint_namespace_session_id(
            session_id,
            language=error_language,
        )

        checkpoint = self.repository.get_checkpoint(namespace_session_id, checkpoint_id)
        if checkpoint is None:
            raise LookupError(
                build_session_action_error_detail(
                    code="session_checkpoint_not_found",
                    message=self._message(
                        "checkpoint_not_found",
                        error_language,
                        checkpoint_id=checkpoint_id,
                    ),
                    scope="session_checkpoint_record",
                    session_id=namespace_session_id,
                    checkpoint_id=checkpoint_id,
                    source_session_id=namespace_session_id,
                )
            )
        self.repository.delete_checkpoint(namespace_session_id, checkpoint_id)
        return DeleteCheckpointResponse(
            message=self._message("checkpoint_deleted", error_language),
            session_id=namespace_session_id,
            checkpoint_id=checkpoint_id,
        )

    def import_checkpoint(
        self,
        payload: dict[str, Any],
        *,
        language_preference: LanguagePreference | None = None,
    ) -> ImportCheckpointResponse:
        requested_language = self._resolve_language(language_preference)
        source_session_id_hint, original_checkpoint_id_hint = self._extract_checkpoint_import_hints(
            payload
        )
        try:
            exported_checkpoint = SessionCheckpointExportPayload.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(
                build_structured_error_detail(
                    code="session_checkpoint_import_invalid_payload",
                    message=self._message(
                        "checkpoint_import_invalid_payload",
                        requested_language,
                    ),
                    scope="session_checkpoint_import_payload",
                    source_session_id=source_session_id_hint,
                    original_checkpoint_id=original_checkpoint_id_hint,
                    errors=shape_validation_error_items(exc.errors()),
                )
            ) from exc

        checkpoint = exported_checkpoint.checkpoint.model_copy(deep=True)
        try:
            SessionState.model_validate(checkpoint.snapshot_payload)
        except ValidationError as exc:
            raise ValueError(
                build_structured_error_detail(
                    code="session_checkpoint_import_invalid_payload",
                    message=self._message(
                        "checkpoint_import_invalid_payload",
                        requested_language,
                    ),
                    scope="session_checkpoint_import_payload",
                    source_session_id=checkpoint.source_session_id,
                    original_checkpoint_id=checkpoint.checkpoint_id,
                    errors=shape_validation_error_items(exc.errors()),
                )
            ) from exc

        original_checkpoint_id = checkpoint.checkpoint_id
        checkpoint.checkpoint_id = f"checkpoint-{uuid4().hex}"
        self.repository.create_checkpoint(checkpoint)
        return ImportCheckpointResponse(
            message=self._message("checkpoint_imported", requested_language),
            checkpoint=SessionCheckpointSummary.model_validate(
                checkpoint.model_dump(exclude={"snapshot_payload"})
            ),
            original_checkpoint_id=original_checkpoint_id,
        )

    def import_session(
        self,
        payload: dict[str, Any],
        *,
        language_preference: LanguagePreference | None = None,
    ) -> SessionImportResponse:
        requested_language = self._resolve_language(language_preference)
        original_session_id_hint = (
            payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        )
        try:
            imported_session = SessionState.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(
                build_structured_error_detail(
                    code="session_import_invalid_snapshot",
                    message=self._message("session_import_invalid_snapshot", requested_language),
                    scope="session_import_payload",
                    original_session_id=original_session_id_hint,
                    errors=shape_validation_error_items(exc.errors()),
                )
            ) from exc
        original_session_id = imported_session.session_id
        original_version = imported_session.state_version
        current_time = datetime.now(timezone.utc)
        diagnostics_language = self._resolve_language(
            language_preference,
            imported_session.language_preference,
            requested_language,
        )

        restored_session = imported_session.model_copy(deep=True)
        restored_session.session_id = f"session-{uuid4().hex}"
        restored_session.state_version = 1
        restored_session.created_at = current_time
        restored_session.updated_at = current_time
        warnings = self._collect_import_warnings(
            restored_session,
            language=diagnostics_language,
        )
        restored_session.timeline.append(
            SessionEvent(
                event_type=EventType.IMPORT,
                actor_type=ActorType.SYSTEM,
                visibility_scope=VisibilityScope.PUBLIC,
                text=f"从存档 {original_session_id} (version {original_version}) 恢复",
                structured_payload={
                    "original_session_id": original_session_id,
                    "original_version": original_version,
                },
                language_preference=restored_session.language_preference,
                created_at=current_time,
            )
        )
        restored_session.audit_log.append(
            AuditLogEntry(
                action=AuditActionType.IMPORT,
                session_version=1,
                details={
                    "original_session_id": original_session_id,
                    "original_version": original_version,
                },
                created_at=current_time,
            )
        )
        try:
            self.repository.create(
                restored_session,
                reason=f"imported_from_{original_session_id}",
            )
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                diagnostics_language,
            )
            raise ConflictError(
                build_structured_error_detail(
                    code="session_import_state_conflict",
                    message=message,
                    scope="session_import_state",
                    original_session_id=original_session_id,
                    original_version=original_version,
                )
            ) from exc
        return SessionImportResponse(
            original_session_id=original_session_id,
            new_session_id=restored_session.session_id,
            state_version=restored_session.state_version,
            warnings=warnings,
        )

    def _collect_import_warnings(
        self,
        session: SessionState,
        *,
        language: LanguagePreference,
    ) -> list[SessionImportWarning]:
        warnings: list[SessionImportWarning] = []
        source_exists_cache: dict[str, bool] = {}

        def source_exists(source_id: str) -> bool:
            if source_id not in source_exists_cache:
                source_exists_cache[source_id] = self._knowledge_source_exists(source_id)
            return source_exists_cache[source_id]

        for participant in session.participants:
            source_id = participant.imported_character_source_id
            if source_id is None or source_exists(source_id):
                continue
            warnings.append(
                SessionImportWarning(
                    code="missing_external_source",
                    scope="participant.imported_character_source_id",
                    ref=f"participants.{participant.actor_id}.imported_character_source_id",
                    source_id=source_id,
                    message=self._message(
                        "session_import_missing_participant_source_warning",
                        language,
                        actor_id=participant.actor_id,
                        source_id=source_id,
                    ),
                )
            )

        for actor_id, character_state in session.character_states.items():
            source_id = character_state.import_source_id
            if source_id is not None and not source_exists(source_id):
                warnings.append(
                    SessionImportWarning(
                        code="missing_external_source",
                        scope="character_state.import_source_id",
                        ref=f"character_states.{actor_id}.import_source_id",
                        source_id=source_id,
                        message=self._message(
                            "session_import_missing_character_state_source_warning",
                            language,
                            actor_id=actor_id,
                            source_id=source_id,
                        ),
                    )
                )

            for ref in dict.fromkeys(character_state.secret_state_refs):
                ref_source_id = self._extract_knowledge_source_id_from_secret_ref(ref)
                if ref_source_id is None or source_exists(ref_source_id):
                    continue
                warnings.append(
                    SessionImportWarning(
                        code="missing_external_source",
                        scope="character_state.secret_state_refs",
                        ref=ref,
                        source_id=ref_source_id,
                        message=self._message(
                            "session_import_missing_secret_source_warning",
                            language,
                            actor_id=actor_id,
                            ref=ref,
                            source_id=ref_source_id,
                        ),
                    )
                )
        return warnings

    def _session_has_missing_external_sources(self, session: SessionState) -> bool:
        source_exists_cache: dict[str, bool] = {}

        def source_exists(source_id: str) -> bool:
            if source_id not in source_exists_cache:
                source_exists_cache[source_id] = self._knowledge_source_exists(source_id)
            return source_exists_cache[source_id]

        for participant in session.participants:
            source_id = participant.imported_character_source_id
            if source_id is not None and not source_exists(source_id):
                return True

        for character_state in session.character_states.values():
            source_id = character_state.import_source_id
            if source_id is not None and not source_exists(source_id):
                return True
            for ref in dict.fromkeys(character_state.secret_state_refs):
                ref_source_id = self._extract_knowledge_source_id_from_secret_ref(ref)
                if ref_source_id is not None and not source_exists(ref_source_id):
                    return True
        return False

    def _is_grounding_degraded(
        self,
        session: SessionState,
        rules_grounding: RuleGroundingSummary | None,
    ) -> bool:
        if rules_grounding is None:
            return False
        if (
            rules_grounding.matched_topics
            or rules_grounding.citations
            or rules_grounding.deterministic_handoff_topic is not None
            or rules_grounding.chinese_answer_draft is not None
        ):
            return False
        return self._session_has_missing_external_sources(session)

    @staticmethod
    def _grounding_degraded_review_summary() -> str:
        return "规则依据降级：当前环境缺少外部知识源，未命中可用规则依据。"

    def _annotate_grounding_review_summary(
        self,
        *,
        session: SessionState,
        rules_grounding: RuleGroundingSummary | None,
    ) -> bool:
        grounding_degraded = self._is_grounding_degraded(session, rules_grounding)
        if grounding_degraded and rules_grounding is not None:
            rules_grounding.review_summary = self._grounding_degraded_review_summary()
        return grounding_degraded

    def _knowledge_source_exists(self, source_id: str) -> bool:
        if self.knowledge_repository is None:
            return False
        return self.knowledge_repository.get_source(source_id) is not None

    @staticmethod
    def _extract_knowledge_source_id_from_secret_ref(ref: str) -> str | None:
        prefix = "knowledge_source:"
        if not ref.startswith(prefix):
            return None
        remainder = ref[len(prefix) :]
        if not remainder:
            return None
        return remainder.split(":", 1)[0]

    def submit_player_action(
        self,
        session_id: str,
        request: PlayerActionRequest,
    ) -> PlayerActionResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="player_action_session_not_found",
                    message=message,
                    scope="player_action_session",
                    session_id=session_id,
                    actor_id=request.actor_id,
                )
            ) from exc
        error_context = {
            "session_id": session.session_id,
            "actor_id": request.actor_id,
        }
        try:
            participant = self._get_participant(session, request.actor_id, language=error_language)
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "actor_not_participant",
                error_language,
                actor_id=request.actor_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="player_action_invalid",
                    message=message,
                    scope="player_action_request",
                    **error_context,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference, session.language_preference
        )
        try:
            current_time = datetime.now(timezone.utc)
            visible_to = self._normalize_visible_to(
                actor_id=request.actor_id,
                visibility_scope=request.visibility_scope,
                visible_to=request.visible_to,
            )
            expected_version = session.state_version
            rules_grounding = self._ground_rules_for_action(
                actor_id=request.actor_id,
                actor_type=ActorType.INVESTIGATOR,
                query_text=self._resolve_rules_query_text(
                    request.rules_query_text,
                    request.action_text,
                    request.structured_action,
                ),
                deterministic_resolution_required=request.deterministic_resolution_required,
            )
            grounding_degraded = self._annotate_grounding_review_summary(
                session=session,
                rules_grounding=rules_grounding,
            )

            resolved_effects, effect_contract_origin = self._resolve_action_effect_contract(
                explicit_effects=request.effects,
                structured_action=request.structured_action,
            )
            if participant.kind == ParticipantKind.AI:
                draft_action = self._build_draft_action(
                    session=session,
                    actor_id=request.actor_id,
                    actor_type=ActorType.INVESTIGATOR,
                    visibility_scope=request.visibility_scope,
                    visible_to=visible_to,
                    draft_text=request.action_text,
                    structured_action=request.structured_action,
                    effects=resolved_effects,
                    effect_contract_origin=effect_contract_origin,
                    rationale_summary=request.rationale_summary
                    or self._message("draft_rationale", effective_language),
                    rules_grounding=rules_grounding,
                    language=effective_language,
                    behavior_context=self._get_behavior_context(session, request.actor_id),
                    current_time=current_time,
                )
                session.draft_actions.append(draft_action)
                session.state_version += 1
                session.updated_at = current_time
                self._append_audit_log(
                    session,
                    action=AuditActionType.DRAFT_CREATED,
                    actor_id=request.actor_id,
                    subject_id=draft_action.draft_id,
                    current_time=current_time,
                    details={
                        "origin": "player_action",
                        "risk_level": draft_action.risk_level.value,
                        "requires_explicit_approval": draft_action.requires_explicit_approval,
                    },
                )
                self._save_session(
                    session,
                    expected_version=expected_version,
                    reason="player_action_draft",
                    language=effective_language,
                )
                return PlayerActionResponse(
                    message=self._message("draft_recorded", effective_language),
                    session_id=session.session_id,
                    state_version=session.state_version,
                    language_preference=effective_language,
                    grounding_degraded=grounding_degraded,
                    draft_action=draft_action,
                )

            authoritative_action = self._build_authoritative_action(
                source_type=AuthoritativeActionSource.HUMAN_PLAYER,
                actor_id=request.actor_id,
                actor_type=ActorType.INVESTIGATOR,
                visibility_scope=request.visibility_scope,
                visible_to=visible_to,
                text=request.action_text,
                structured_action=request.structured_action,
                effects=resolved_effects,
                effect_contract_origin=effect_contract_origin,
                rules_grounding=rules_grounding,
                language_preference=effective_language,
                created_at=current_time,
            )
            authoritative_event = self._apply_authoritative_action(
                session=session,
                authoritative_action=authoritative_action,
                event_type=EventType.PLAYER_ACTION,
                language=effective_language,
                current_time=current_time,
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="player_action",
                language=effective_language,
            )
            return PlayerActionResponse(
                message=self._message("player_action_recorded", effective_language),
                session_id=session.session_id,
                state_version=session.state_version,
                language_preference=effective_language,
                grounding_degraded=grounding_degraded,
                authoritative_event=authoritative_event,
                authoritative_action=authoritative_action,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                effective_language,
                session_id=session.session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="player_action_target_not_found",
                    message=message,
                    scope="player_action_execution",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="player_action_state_conflict",
                    message=message,
                    scope="player_action_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "invalid_scene_transition",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="player_action_invalid",
                    message=message,
                    scope="player_action_execution",
                    **error_context,
                )
            ) from exc

    def perform_investigator_skill_check(
        self,
        session_id: str,
        request: InvestigatorSkillCheckRequest,
    ) -> InvestigatorSkillCheckResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="skill_check_session_not_found",
                    message=message,
                    scope="skill_check_session",
                    session_id=session_id,
                    actor_id=request.actor_id,
                )
            ) from exc

        error_context = {
            "session_id": session.session_id,
            "actor_id": request.actor_id,
            "skill_name": request.skill_name,
        }
        try:
            participant = self._get_participant(session, request.actor_id, language=error_language)
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "actor_not_participant",
                error_language,
                actor_id=request.actor_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="skill_check_invalid",
                    message=message,
                    scope="skill_check_request",
                    **error_context,
                )
            ) from exc

        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        try:
            if session.status == SessionStatus.COMPLETED:
                raise ValueError(self._message("skill_check_session_completed", effective_language))

            normalized_skill_name = request.skill_name.strip()
            skill_scores = dict(participant.character.skills)
            character_state = session.character_states.get(request.actor_id)
            if character_state is not None:
                for skill_name, score in character_state.skill_baseline.items():
                    skill_scores.setdefault(skill_name, score)
            if not skill_scores:
                raise ValueError(self._message("skill_check_no_skills", effective_language))
            if normalized_skill_name not in skill_scores:
                raise ValueError(
                    self._message(
                        "skill_check_skill_not_found",
                        effective_language,
                        skill_name=normalized_skill_name,
                    )
                )

            skill_value = int(skill_scores[normalized_skill_name])
            roll = roll_d100(skill_value)
            return InvestigatorSkillCheckResponse(
                message=self._message("skill_check_recorded", effective_language),
                session_id=session.session_id,
                viewer_id=request.actor_id,
                state_version=session.state_version,
                language_preference=effective_language,
                skill_name=normalized_skill_name,
                skill_value=skill_value,
                roll=roll,
                success=roll.total <= skill_value,
            )
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "invalid_scene_transition",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="skill_check_invalid",
                    message=message,
                    scope="skill_check_request",
                    **error_context,
                )
            ) from exc

    def perform_investigator_attribute_check(
        self,
        session_id: str,
        request: InvestigatorAttributeCheckRequest,
    ) -> InvestigatorAttributeCheckResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="attribute_check_session_not_found",
                    message=message,
                    scope="attribute_check_session",
                    session_id=session_id,
                    actor_id=request.actor_id,
                )
            ) from exc

        error_context = {
            "session_id": session.session_id,
            "actor_id": request.actor_id,
            "attribute_name": request.attribute_name,
        }
        try:
            participant = self._get_participant(session, request.actor_id, language=error_language)
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "actor_not_participant",
                error_language,
                actor_id=request.actor_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="attribute_check_invalid",
                    message=message,
                    scope="attribute_check_request",
                    **error_context,
                )
            ) from exc

        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        try:
            if session.status == SessionStatus.COMPLETED:
                raise ValueError(self._message("attribute_check_session_completed", effective_language))

            normalized_attribute_name = request.attribute_name.strip()
            attribute_scores = participant.character.attributes.model_dump(mode="json")
            if normalized_attribute_name not in attribute_scores:
                raise ValueError(
                    self._message(
                        "attribute_check_attribute_not_found",
                        effective_language,
                        attribute_name=normalized_attribute_name,
                    )
                )

            attribute_value = int(attribute_scores[normalized_attribute_name])
            roll = roll_d100(attribute_value)
            return InvestigatorAttributeCheckResponse(
                message=self._message("attribute_check_recorded", effective_language),
                session_id=session.session_id,
                viewer_id=request.actor_id,
                state_version=session.state_version,
                language_preference=effective_language,
                attribute_name=normalized_attribute_name,
                attribute_value=attribute_value,
                roll=roll,
                success=roll.total <= attribute_value,
            )
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "invalid_scene_transition",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="attribute_check_invalid",
                    message=message,
                    scope="attribute_check_request",
                    **error_context,
                )
            ) from exc

    def _normalize_san_loss_expression(
        self,
        expression: str,
        *,
        language: LanguagePreference,
    ) -> str:
        normalized = expression.strip().lower()
        match = _SAN_LOSS_EXPRESSION_PATTERN.fullmatch(normalized)
        if match is None:
            raise ValueError(
                self._message(
                    "san_check_loss_invalid",
                    language,
                    expression=expression,
                )
            )
        static_value, dice_count, dice_sides = match.groups()
        if static_value is not None:
            return str(int(static_value))
        count = int(dice_count or "0")
        sides = int(dice_sides or "0")
        if count <= 0 or sides <= 0:
            raise ValueError(
                self._message(
                    "san_check_loss_invalid",
                    language,
                    expression=expression,
                )
            )
        return f"{count}d{sides}"

    def _queue_san_aftermath_prompt(
        self,
        *,
        session: SessionState,
        participant: SessionParticipant,
        source_label: str,
        previous_sanity: int,
        current_sanity: int,
        loss_applied: int,
        current_time: datetime,
        language: LanguagePreference,
    ) -> None:
        session.progress_state.queued_kp_prompts.append(
            QueuedKPPrompt(
                prompt_text=self._message(
                    "san_aftermath_prompt_text",
                    language,
                    actor_name=participant.display_name,
                    source_label=source_label,
                ),
                scene_id=session.current_scene.scene_id,
                source_action_id=None,
                category="san_aftermath",
                priority=KeeperPromptPriority.MEDIUM,
                assigned_to=session.keeper_id,
                beat_id=session.progress_state.current_beat,
                notes=[],
                status=KeeperPromptStatus.PENDING,
                san_actor_id=participant.actor_id,
                san_actor_name=participant.display_name,
                san_source_label=source_label,
                san_previous_sanity=previous_sanity,
                san_current_sanity=current_sanity,
                san_loss_applied=loss_applied,
                trigger_reason=self._message(
                    "san_aftermath_prompt_reason",
                    language,
                    previous_sanity=previous_sanity,
                    current_sanity=current_sanity,
                    loss_applied=loss_applied,
                ),
                created_at=current_time,
                updated_at=current_time,
            )
        )
        session.progress_state.last_updated_at = current_time

    def perform_investigator_san_check(
        self,
        session_id: str,
        request: InvestigatorSanCheckRequest,
    ) -> InvestigatorSanCheckResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="san_check_session_not_found",
                    message=message,
                    scope="san_check_session",
                    session_id=session_id,
                    actor_id=request.actor_id,
                )
            ) from exc

        error_context = {
            "session_id": session.session_id,
            "actor_id": request.actor_id,
            "source_label": request.source_label,
        }
        try:
            participant = self._get_participant(session, request.actor_id, language=error_language)
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "actor_not_participant",
                error_language,
                actor_id=request.actor_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="san_check_invalid",
                    message=message,
                    scope="san_check_request",
                    **error_context,
                )
            ) from exc

        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        try:
            if session.status == SessionStatus.COMPLETED:
                raise ValueError(self._message("san_check_session_completed", effective_language))

            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            source_label = request.source_label.strip()
            success_loss = self._normalize_san_loss_expression(
                request.success_loss,
                language=effective_language,
            )
            failure_loss = self._normalize_san_loss_expression(
                request.failure_loss,
                language=effective_language,
            )
            character_state = self._ensure_character_state(
                session,
                actor_id=request.actor_id,
                current_time=current_time,
                language=effective_language,
            )
            previous_sanity = int(character_state.current_sanity)
            if previous_sanity <= 0:
                raise ValueError(self._message("san_check_no_sanity_remaining", effective_language))

            roll = roll_d100(previous_sanity)
            success = roll.total <= previous_sanity
            applied_loss_expression = success_loss if success else failure_loss
            resolved_sanity_loss = _roll_san_loss_value(applied_loss_expression)
            current_sanity = max(0, previous_sanity - resolved_sanity_loss)
            character_state.current_sanity = current_sanity
            character_state.last_updated_at = current_time
            if resolved_sanity_loss > 0:
                self._queue_san_aftermath_prompt(
                    session=session,
                    participant=participant,
                    source_label=source_label,
                    previous_sanity=previous_sanity,
                    current_sanity=current_sanity,
                    loss_applied=resolved_sanity_loss,
                    current_time=current_time,
                    language=effective_language,
                )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="investigator_san_check",
                language=effective_language,
            )
            return InvestigatorSanCheckResponse(
                message=self._message("san_check_recorded", effective_language),
                session_id=session.session_id,
                viewer_id=request.actor_id,
                state_version=session.state_version,
                language_preference=effective_language,
                source_label=source_label,
                previous_sanity=previous_sanity,
                current_sanity=current_sanity,
                success_loss=success_loss,
                failure_loss=failure_loss,
                applied_loss_expression=applied_loss_expression,
                resolved_sanity_loss=resolved_sanity_loss,
                roll=roll,
                success=success,
            )
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="san_check_state_conflict",
                    message=message,
                    scope="san_check_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "san_check_invalid",
                effective_language,
                source_label=request.source_label,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="san_check_invalid",
                    message=message,
                    scope="san_check_request",
                    **error_context,
                )
            ) from exc

        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        try:
            if session.status == SessionStatus.COMPLETED:
                raise ValueError(self._message("attribute_check_session_completed", effective_language))

            normalized_attribute_name = request.attribute_name.strip()
            attribute_scores = participant.character.attributes.model_dump(mode="json")
            if normalized_attribute_name not in attribute_scores:
                raise ValueError(
                    self._message(
                        "attribute_check_attribute_not_found",
                        effective_language,
                        attribute_name=normalized_attribute_name,
                    )
                )

            attribute_value = int(attribute_scores[normalized_attribute_name])
            roll = roll_d100(attribute_value)
            return InvestigatorAttributeCheckResponse(
                message=self._message("attribute_check_recorded", effective_language),
                session_id=session.session_id,
                viewer_id=request.actor_id,
                state_version=session.state_version,
                language_preference=effective_language,
                attribute_name=normalized_attribute_name,
                attribute_value=attribute_value,
                roll=roll,
                success=roll.total <= attribute_value,
            )
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "invalid_scene_transition",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="attribute_check_invalid",
                    message=message,
                    scope="attribute_check_request",
                    **error_context,
                )
            ) from exc

    def submit_kp_draft(
        self,
        session_id: str,
        request: KPDraftRequest,
    ) -> PlayerActionResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="kp_draft_session_not_found",
                    message=message,
                    scope="kp_draft_session",
                    session_id=session_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference, session.language_preference
        )
        error_context = {
            "session_id": session.session_id,
            "actor_id": session.keeper_id,
        }
        try:
            current_time = datetime.now(timezone.utc)
            visible_to = self._normalize_visible_to(
                actor_id=session.keeper_id,
                visibility_scope=request.visibility_scope,
                visible_to=request.visible_to,
            )
            expected_version = session.state_version
            rules_grounding = self._ground_rules_for_action(
                actor_id=session.keeper_id,
                actor_type=ActorType.KEEPER,
                query_text=self._resolve_rules_query_text(
                    request.rules_query_text,
                    request.draft_text,
                    request.structured_action,
                ),
                deterministic_resolution_required=request.deterministic_resolution_required,
            )
            grounding_degraded = self._annotate_grounding_review_summary(
                session=session,
                rules_grounding=rules_grounding,
            )
            resolved_effects, effect_contract_origin = self._resolve_action_effect_contract(
                explicit_effects=request.effects,
                structured_action=request.structured_action,
            )
            draft_action = self._build_draft_action(
                session=session,
                actor_id=session.keeper_id,
                actor_type=ActorType.KEEPER,
                visibility_scope=request.visibility_scope,
                visible_to=visible_to,
                draft_text=request.draft_text,
                structured_action=request.structured_action,
                effects=resolved_effects,
                effect_contract_origin=effect_contract_origin,
                rationale_summary=request.rationale_summary
                or self._message("kp_draft_rationale", effective_language),
                rules_grounding=rules_grounding,
                language=effective_language,
                behavior_context=[],
                current_time=current_time,
            )
            session.draft_actions.append(draft_action)
            session.state_version += 1
            session.updated_at = current_time
            self._append_audit_log(
                session,
                action=AuditActionType.DRAFT_CREATED,
                actor_id=session.keeper_id,
                subject_id=draft_action.draft_id,
                current_time=current_time,
                details={"origin": "kp_draft", "risk_level": draft_action.risk_level.value},
            )
            self._save_session(
                session,
                expected_version=expected_version,
                reason="kp_draft_created",
                language=effective_language,
            )
            return PlayerActionResponse(
                message=self._message("kp_draft_recorded", effective_language),
                session_id=session.session_id,
                state_version=session.state_version,
                language_preference=effective_language,
                grounding_degraded=grounding_degraded,
                draft_action=draft_action,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "draft_not_found",
                effective_language,
                draft_id="unknown",
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="kp_draft_target_not_found",
                    message=message,
                    scope="kp_draft_execution",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="kp_draft_state_conflict",
                    message=message,
                    scope="kp_draft_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "invalid_scene_transition",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="kp_draft_invalid",
                    message=message,
                    scope="kp_draft_request",
                    **error_context,
                )
            ) from exc

    def submit_manual_action(
        self,
        session_id: str,
        request: ManualActionRequest,
    ) -> PlayerActionResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="manual_action_session_not_found",
                    message=message,
                    scope="manual_action_session",
                    session_id=session_id,
                    actor_id=request.actor_id,
                    operator_id=request.operator_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        actor_id = request.actor_id or session.keeper_id
        error_context = {
            "session_id": session.session_id,
            "actor_id": actor_id,
            "operator_id": request.operator_id,
        }
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="manual_action_operator_not_authorized",
                message=self._message("operator_not_authorized", effective_language),
                scope="manual_action_permission",
                **error_context,
            ),
        )
        try:
            current_time = datetime.now(timezone.utc)
            visible_to = self._normalize_visible_to(
                actor_id=actor_id,
                visibility_scope=request.visibility_scope,
                visible_to=request.visible_to,
            )
            expected_version = session.state_version
            rules_grounding = self._ground_rules_for_action(
                actor_id=actor_id,
                actor_type=request.actor_type,
                query_text=self._resolve_rules_query_text(
                    request.rules_query_text,
                    request.action_text,
                    request.structured_action,
                ),
                deterministic_resolution_required=request.deterministic_resolution_required,
            )
            grounding_degraded = self._annotate_grounding_review_summary(
                session=session,
                rules_grounding=rules_grounding,
            )
            resolved_effects, effect_contract_origin = self._resolve_action_effect_contract(
                explicit_effects=request.effects,
                structured_action=request.structured_action,
            )
            authoritative_action = self._build_authoritative_action(
                source_type=AuthoritativeActionSource.MANUAL_OPERATOR,
                actor_id=actor_id,
                actor_type=request.actor_type,
                visibility_scope=request.visibility_scope,
                visible_to=visible_to,
                text=request.action_text,
                structured_action=request.structured_action,
                effects=resolved_effects,
                effect_contract_origin=effect_contract_origin,
                rules_grounding=rules_grounding,
                language_preference=effective_language,
                created_at=current_time,
            )
            authoritative_event = self._apply_authoritative_action(
                session=session,
                authoritative_action=authoritative_action,
                event_type=EventType.MANUAL_ACTION,
                language=effective_language,
                current_time=current_time,
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="manual_action",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                effective_language,
                session_id=session.session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="manual_action_target_not_found",
                    message=message,
                    scope="manual_action_execution",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="manual_action_state_conflict",
                    message=message,
                    scope="manual_action_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "invalid_scene_transition",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="manual_action_invalid",
                    message=message,
                    scope="manual_action_execution",
                    **error_context,
                )
            ) from exc
        return PlayerActionResponse(
            message=self._message("manual_action_recorded", effective_language),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            grounding_degraded=grounding_degraded,
            authoritative_event=authoritative_event,
            authoritative_action=authoritative_action,
        )

    def apply_character_import(
        self,
        session_id: str,
        request: ApplyCharacterImportRequest,
    ) -> ApplyCharacterImportResponse:
        error_language = self._resolve_language(request.language_preference)
        error_context = {
            "source_id": request.source_id,
            "session_id": session_id,
            "actor_id": request.actor_id,
        }
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_character_import_error_detail(
                    code="character_import_session_not_found",
                    message=message,
                    scope="character_import_session",
                    **error_context,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        error_context["session_id"] = session.session_id
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_character_import_error_detail(
                code="character_import_operator_not_authorized",
                message=self._message(
                    "character_import_operator_not_authorized",
                    effective_language,
                ),
                **error_context,
                operator_id=request.operator_id,
                scope="character_import_permission",
            ),
        )
        try:
            source = self._load_character_import_source(
                request.source_id,
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "character_import_source_not_found",
                effective_language,
                source_id=request.source_id,
            )
            raise LookupError(
                build_character_import_error_detail(
                    code="character_import_source_not_found",
                    message=message,
                    scope="character_import_source",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            not_supported_message = self._message(
                "character_import_not_supported",
                effective_language,
            )
            missing_extraction_message = self._message(
                "character_import_missing_extraction",
                effective_language,
                source_id=request.source_id,
            )
            if exc.args and exc.args[0] == not_supported_message:
                raise ValueError(
                    build_character_import_error_detail(
                        code="character_import_not_supported",
                        message=not_supported_message,
                        scope="character_import_support",
                        **error_context,
                    )
                ) from exc
            if exc.args and exc.args[0] == missing_extraction_message:
                raise ValueError(
                    build_character_import_error_detail(
                        code="character_import_missing_extraction",
                        message=missing_extraction_message,
                        scope="character_import_source",
                        **error_context,
                    )
                ) from exc
            raise
        current_time = datetime.now(timezone.utc)
        expected_version = session.state_version
        sync_policy = self._resolve_character_import_sync_policy(
            request.sync_policy,
            refresh_existing=request.refresh_existing,
        )
        try:
            character_state, sync_report = self._apply_character_sheet_extraction_to_session(
                session,
                actor_id=request.actor_id,
                source=source,
                sync_policy=sync_policy,
                force_apply_manual_review=request.force_apply_manual_review,
                current_time=current_time,
                language=effective_language,
            )
        except ValueError as exc:
            force_review_required_message = self._message(
                "character_import_force_review_required",
                effective_language,
            )
            if exc.args and exc.args[0] == force_review_required_message:
                raise ValueError(
                    build_character_import_error_detail(
                        code="character_import_force_review_required",
                        message=force_review_required_message,
                        scope="character_import_review",
                        **error_context,
                    )
                ) from exc
            raise
        session.state_version += 1
        session.updated_at = current_time
        try:
            self._save_session(
                session,
                expected_version=expected_version,
                reason="character_import_applied",
                language=effective_language,
            )
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_character_import_error_detail(
                    code="character_import_state_conflict",
                    message=message,
                    scope="character_import_state",
                    **error_context,
                )
            ) from exc
        return ApplyCharacterImportResponse(
            message=self._message("character_import_applied", effective_language),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            character_state=character_state,
            sync_report=sync_report,
        )

    def update_keeper_prompt_status(
        self,
        session_id: str,
        prompt_id: str,
        request: UpdateKeeperPromptRequest,
    ) -> UpdateKeeperPromptResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_prompt_session_not_found",
                    message=message,
                    scope="keeper_prompt_session",
                    session_id=session_id,
                    operator_id=request.operator_id,
                    prompt_id=prompt_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        error_context = {
            "session_id": session.session_id,
            "operator_id": request.operator_id,
            "prompt_id": prompt_id,
        }
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="keeper_prompt_operator_not_authorized",
                message=self._message("keeper_prompt_operator_not_authorized", effective_language),
                scope="keeper_prompt_permission",
                **error_context,
            ),
        )
        try:
            prompt = self._get_keeper_prompt(session, prompt_id, language=effective_language)
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            self._update_keeper_prompt(
                prompt,
                status=request.status,
                add_notes=request.add_notes,
                priority=request.priority,
                assigned_to=request.assigned_to,
                aftermath_label=request.aftermath_label,
                duration_rounds=request.duration_rounds,
                current_time=current_time,
                language=effective_language,
            )
            self._append_audit_log(
                session,
                action=AuditActionType.KEEPER_PROMPT_UPDATED,
                actor_id=request.operator_id,
                subject_id=prompt.prompt_id,
                current_time=current_time,
                details={
                    "status": request.status.value if request.status is not None else None,
                    "priority": request.priority.value if request.priority is not None else None,
                    "assigned_to": request.assigned_to,
                    "aftermath_label": request.aftermath_label,
                    "duration_rounds": request.duration_rounds,
                    "note_count_added": len(request.add_notes),
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_prompt_status_updated",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "keeper_prompt_not_found",
                effective_language,
                prompt_id=prompt_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_prompt_not_found",
                    message=message,
                    scope="keeper_prompt_prompt",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="keeper_prompt_state_conflict",
                    message=message,
                    scope="keeper_prompt_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "keeper_prompt_status_invalid",
                effective_language,
                from_status="pending",
                to_status="invalid",
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_prompt_invalid",
                    message=message,
                    scope="keeper_prompt_update",
                    **error_context,
                )
            ) from exc
        keeper_workflow = filter_session_for_viewer(
            session,
            viewer_id=None,
            viewer_role=ViewerRole.KEEPER,
        ).keeper_workflow
        response_prompt = normalize_keeper_prompt_for_keeper(session, prompt)
        return UpdateKeeperPromptResponse(
            message=self._message("keeper_prompt_updated", effective_language),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            prompt=response_prompt,
            keeper_workflow=keeper_workflow or KeeperWorkflowState(),
        )

    def _load_keeper_live_control_context(
        self,
        session_id: str,
        request: KeeperLiveControlRequest,
    ) -> tuple[SessionState, LanguagePreference, dict[str, Any]]:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_live_control_session_not_found",
                    message=message,
                    scope="keeper_live_control_session",
                    session_id=session_id,
                    operator_id=request.operator_id,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        error_context = {
            "session_id": session.session_id,
            "operator_id": request.operator_id,
        }
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="keeper_live_control_operator_not_authorized",
                message=self._message("operator_not_authorized", effective_language),
                scope="keeper_live_control_permission",
                **error_context,
            ),
        )
        if session.status == SessionStatus.COMPLETED:
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_live_control_invalid",
                    message=self._message(
                        "keeper_live_control_completed",
                        effective_language,
                    ),
                    scope="keeper_live_control_session",
                    session_status=session.status.value,
                    **error_context,
                )
            )
        return session, effective_language, error_context

    def _load_keeper_lifecycle_context(
        self,
        session_id: str,
        request: UpdateSessionLifecycleRequest,
    ) -> tuple[SessionState, LanguagePreference, dict[str, Any]]:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="session_lifecycle_session_not_found",
                    message=message,
                    scope="session_lifecycle_session",
                    session_id=session_id,
                    operator_id=request.operator_id,
                    target_status=request.target_status.value,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        error_context = {
            "session_id": session.session_id,
            "operator_id": request.operator_id,
            "target_status": request.target_status.value,
        }
        self._authorize_operator(
            session,
            operator_id=request.operator_id,
            language=effective_language,
            error_detail=build_session_action_error_detail(
                code="session_lifecycle_operator_not_authorized",
                message=self._message(
                    "session_lifecycle_operator_not_authorized",
                    effective_language,
                ),
                scope="session_lifecycle_permission",
                **error_context,
            ),
        )
        return session, effective_language, error_context

    def _append_keeper_live_control_record(
        self,
        *,
        session: SessionState,
        operator_id: str,
        current_time: datetime,
        language: LanguagePreference,
        target_id: str,
        control_type: str,
        event_text: str,
        details: dict[str, Any],
    ) -> None:
        session.timeline.append(
            SessionEvent(
                event_type=EventType.MANUAL_ACTION,
                actor_id=operator_id,
                actor_type=ActorType.KEEPER,
                visibility_scope=VisibilityScope.KP_ONLY,
                visible_to=[],
                text=event_text,
                structured_payload={
                    "control_type": control_type,
                    **details,
                },
                is_authoritative=True,
                language_preference=language,
                created_at=current_time,
            )
        )
        self._append_audit_log(
            session,
            action=AuditActionType.KEEPER_LIVE_CONTROL,
            actor_id=operator_id,
            subject_id=target_id,
            current_time=current_time,
            details={
                "control_type": control_type,
                **details,
            },
        )

    @staticmethod
    def _get_active_scene_objective(
        session: SessionState,
        objective_id: str,
    ) -> ActiveSceneObjective | None:
        for objective in session.progress_state.active_scene_objectives:
            if objective.objective_id == objective_id:
                return objective
        return None

    def _reopen_scene_objective(
        self,
        *,
        session: SessionState,
        objective: ActiveSceneObjective,
        current_time: datetime,
    ) -> None:
        objective.resolved = False
        objective.resolved_by_action_id = None
        matching_indices = [
            index
            for index, record in enumerate(session.progress_state.completed_objective_history)
            if record.objective_id == objective.objective_id
        ]
        if matching_indices:
            del session.progress_state.completed_objective_history[matching_indices[-1]]
        if not any(
            record.text == objective.text
            for record in session.progress_state.completed_objective_history
        ):
            session.progress_state.completed_objectives = [
                value
                for value in session.progress_state.completed_objectives
                if value != objective.text
            ]
            session.progress_state.completed_scene_objectives = [
                value
                for value in session.progress_state.completed_scene_objectives
                if value != objective.text
            ]
        session.progress_state.last_updated_at = current_time

    def _list_keeper_next_beats(
        self,
        session: SessionState,
    ) -> list[ScenarioBeat]:
        current_beat_id = session.progress_state.current_beat
        if current_beat_id is None:
            return []
        current_beat = self._find_beat(session.scenario.beats, current_beat_id)
        candidates: list[ScenarioBeat] = []
        for next_beat_id in current_beat.next_beats:
            target_beat = self._find_beat(session.scenario.beats, next_beat_id)
            target_status = self._resolve_beat_status(session, next_beat_id)
            if target_status in {
                ScenarioBeatStatus.BLOCKED,
                ScenarioBeatStatus.COMPLETED,
                ScenarioBeatStatus.CURRENT,
            }:
                continue
            candidates.append(target_beat)
        return candidates

    def complete_keeper_objective(
        self,
        session_id: str,
        objective_id: str,
        request: KeeperLiveControlRequest,
    ) -> KeeperLiveControlResponse:
        session, effective_language, error_context = self._load_keeper_live_control_context(
            session_id,
            request,
        )
        error_context["objective_id"] = objective_id
        try:
            objective = self._get_active_scene_objective(session, objective_id)
            if objective is None:
                raise LookupError(
                    self._message(
                        "objective_not_found",
                        effective_language,
                        objective_id=objective_id,
                    )
                )
            if objective.resolved:
                raise ValueError(
                    self._message(
                        "objective_already_completed",
                        effective_language,
                        objective=objective.text,
                    )
                )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            source_action_id = f"keeper-control-{uuid4().hex}"
            completed_objectives = self._mark_scene_objective_complete(
                session=session,
                source_action_id=source_action_id,
                beat_id=objective.beat_id,
                scene_id=objective.scene_id,
                objective_id=objective.objective_id,
                objective_label=objective.text,
                trigger_reason=self._message(
                    "scene_objective_completed_by_keeper",
                    effective_language,
                ),
                current_time=current_time,
            )
            objective_label = completed_objectives[0] if completed_objectives else objective.text
            event_text = self._message(
                "keeper_live_control_objective_completed",
                effective_language,
                objective=objective_label,
            )
            self._append_keeper_live_control_record(
                session=session,
                operator_id=request.operator_id,
                current_time=current_time,
                language=effective_language,
                target_id=objective.objective_id,
                control_type="objective_complete",
                event_text=event_text,
                details={
                    "objective_id": objective.objective_id,
                    "objective_text": objective.text,
                    "scene_id": objective.scene_id,
                    "beat_id": objective.beat_id,
                    "result": "completed",
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_live_control",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "objective_not_found",
                effective_language,
                objective_id=objective_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_live_control_objective_not_found",
                    message=message,
                    scope="keeper_live_control_objective",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="keeper_live_control_state_conflict",
                    message=message,
                    scope="keeper_live_control_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "objective_already_completed",
                effective_language,
                objective=objective_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_live_control_invalid",
                    message=message,
                    scope="keeper_live_control_objective",
                    **error_context,
                )
            ) from exc
        return KeeperLiveControlResponse(
            message=self._message(
                "keeper_live_control_objective_completed",
                effective_language,
                objective=objective.text,
            ),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            target_id=objective.objective_id,
            target_type="objective",
        )

    def reopen_keeper_objective(
        self,
        session_id: str,
        objective_id: str,
        request: KeeperLiveControlRequest,
    ) -> KeeperLiveControlResponse:
        session, effective_language, error_context = self._load_keeper_live_control_context(
            session_id,
            request,
        )
        error_context["objective_id"] = objective_id
        try:
            objective = self._get_active_scene_objective(session, objective_id)
            if objective is None:
                raise LookupError(
                    self._message(
                        "objective_not_found",
                        effective_language,
                        objective_id=objective_id,
                    )
                )
            if not objective.resolved:
                raise ValueError(
                    self._message(
                        "objective_not_completed",
                        effective_language,
                        objective=objective.text,
                    )
                )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            self._reopen_scene_objective(
                session=session,
                objective=objective,
                current_time=current_time,
            )
            event_text = self._message(
                "keeper_live_control_objective_reopened",
                effective_language,
                objective=objective.text,
            )
            self._append_keeper_live_control_record(
                session=session,
                operator_id=request.operator_id,
                current_time=current_time,
                language=effective_language,
                target_id=objective.objective_id,
                control_type="objective_reopen",
                event_text=event_text,
                details={
                    "objective_id": objective.objective_id,
                    "objective_text": objective.text,
                    "scene_id": objective.scene_id,
                    "beat_id": objective.beat_id,
                    "result": "reopened",
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_live_control",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "objective_not_found",
                effective_language,
                objective_id=objective_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_live_control_objective_not_found",
                    message=message,
                    scope="keeper_live_control_objective",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="keeper_live_control_state_conflict",
                    message=message,
                    scope="keeper_live_control_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "objective_not_completed",
                effective_language,
                objective=objective_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_live_control_invalid",
                    message=message,
                    scope="keeper_live_control_objective",
                    **error_context,
                )
            ) from exc
        return KeeperLiveControlResponse(
            message=self._message(
                "keeper_live_control_objective_reopened",
                effective_language,
                objective=objective.text,
            ),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            target_id=objective.objective_id,
            target_type="objective",
        )

    def reveal_keeper_clue(
        self,
        session_id: str,
        clue_id: str,
        request: KeeperLiveControlRequest,
    ) -> KeeperLiveControlResponse:
        session, effective_language, error_context = self._load_keeper_live_control_context(
            session_id,
            request,
        )
        error_context["clue_id"] = clue_id
        try:
            clue = self._find_clue(session, clue_id=clue_id, clue_title=None)
            if clue is None:
                raise LookupError(
                    self._message("clue_not_found", effective_language, clue_ref=clue_id)
                )
            if clue.status == ClueProgressState.SHARED_WITH_PARTY and clue.visibility_scope == VisibilityScope.PUBLIC:
                raise ValueError(
                    self._message(
                        "clue_already_revealed",
                        effective_language,
                        title=clue.title,
                    )
                )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            self._apply_clue_state_effect(
                session=session,
                effect=ClueStateEffect(
                    clue_id=clue_id,
                    status=ClueProgressState.SHARED_WITH_PARTY,
                    share_with_party=True,
                    discovered_via=f"keeper_live_control:{clue_id}",
                ),
                language=effective_language,
                current_time=current_time,
            )
            event_text = self._message(
                "keeper_live_control_clue_revealed",
                effective_language,
                title=clue.title,
            )
            self._append_keeper_live_control_record(
                session=session,
                operator_id=request.operator_id,
                current_time=current_time,
                language=effective_language,
                target_id=clue.clue_id,
                control_type="reveal_clue",
                event_text=event_text,
                details={
                    "clue_id": clue.clue_id,
                    "clue_title": clue.title,
                    "result": "revealed",
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_live_control",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "clue_not_found",
                effective_language,
                clue_ref=clue_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_live_control_clue_not_found",
                    message=message,
                    scope="keeper_live_control_clue",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="keeper_live_control_state_conflict",
                    message=message,
                    scope="keeper_live_control_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "clue_already_revealed",
                effective_language,
                title=clue_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_live_control_invalid",
                    message=message,
                    scope="keeper_live_control_clue",
                    **error_context,
                )
            ) from exc
        return KeeperLiveControlResponse(
            message=self._message(
                "keeper_live_control_clue_revealed",
                effective_language,
                title=clue.title,
            ),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            target_id=clue.clue_id,
            target_type="clue",
        )

    def reveal_keeper_scene(
        self,
        session_id: str,
        scene_id: str,
        request: KeeperLiveControlRequest,
    ) -> KeeperLiveControlResponse:
        session, effective_language, error_context = self._load_keeper_live_control_context(
            session_id,
            request,
        )
        error_context["scene_id"] = scene_id
        try:
            scene = self._find_scenario_scene(
                session.scenario.scenes,
                scene_id=scene_id,
                scene_title=None,
            )
            if scene is None:
                raise LookupError(
                    self._message(
                        "scene_not_found",
                        effective_language,
                        scene_id=scene_id,
                    )
                )
            if scene.revealed:
                raise ValueError(
                    self._message(
                        "scene_already_revealed",
                        effective_language,
                        title=scene.title,
                    )
                )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            source_action_id = f"keeper-control-{uuid4().hex}"
            self._reveal_scene_registry_entry(
                session=session,
                scene=scene,
                source_action_id=source_action_id,
                trigger_reason=self._message(
                    "scene_revealed_by_keeper",
                    effective_language,
                ),
                current_time=current_time,
            )
            event_text = self._message(
                "keeper_live_control_scene_revealed",
                effective_language,
                title=scene.title,
            )
            self._append_keeper_live_control_record(
                session=session,
                operator_id=request.operator_id,
                current_time=current_time,
                language=effective_language,
                target_id=scene.scene_id,
                control_type="reveal_scene",
                event_text=event_text,
                details={
                    "scene_id": scene.scene_id,
                    "scene_title": scene.title,
                    "result": "revealed",
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_live_control",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "scene_not_found",
                effective_language,
                scene_id=scene_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_live_control_scene_not_found",
                    message=message,
                    scope="keeper_live_control_scene",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="keeper_live_control_state_conflict",
                    message=message,
                    scope="keeper_live_control_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "scene_already_revealed",
                effective_language,
                title=scene_id,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_live_control_invalid",
                    message=message,
                    scope="keeper_live_control_scene",
                    **error_context,
                )
            ) from exc
        return KeeperLiveControlResponse(
            message=self._message(
                "keeper_live_control_scene_revealed",
                effective_language,
                title=scene.title,
            ),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            target_id=scene.scene_id,
            target_type="scene",
        )

    def advance_keeper_beat(
        self,
        session_id: str,
        beat_id: str,
        request: KeeperLiveControlRequest,
    ) -> KeeperLiveControlResponse:
        session, effective_language, error_context = self._load_keeper_live_control_context(
            session_id,
            request,
        )
        error_context["beat_id"] = beat_id
        try:
            current_beat_id = session.progress_state.current_beat
            if current_beat_id is None:
                raise ValueError(
                    self._message("beat_progression_not_available", effective_language)
                )
            current_beat = self._find_beat(session.scenario.beats, current_beat_id)
            target_beat = self._find_beat(session.scenario.beats, beat_id)
            if target_beat.beat_id == current_beat.beat_id:
                raise ValueError(
                    self._message(
                        "beat_already_current",
                        effective_language,
                        title=target_beat.title,
                    )
                )
            next_beat_ids = {
                candidate.beat_id for candidate in self._list_keeper_next_beats(session)
            }
            if target_beat.beat_id not in next_beat_ids:
                raise ValueError(
                    self._message(
                        "keeper_live_control_beat_not_reachable",
                        effective_language,
                        title=target_beat.title,
                    )
                )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            trigger_action_id = f"keeper-control-{uuid4().hex}"
            if target_beat.beat_id not in session.progress_state.unlocked_beats:
                session.progress_state.unlocked_beats.append(target_beat.beat_id)
            session.progress_state.current_beat = target_beat.beat_id
            self._sync_beat_statuses(session)
            self._register_beat_objective(
                session=session,
                beat=target_beat,
                source_action_id=trigger_action_id,
                trigger_reason=self._message(
                    "beat_reason_keeper_selected_next",
                    effective_language,
                ),
            )
            session.progress_state.transition_history.append(
                self._build_beat_transition_record(
                    beat=target_beat,
                    transition=ScenarioBeatTransitionType.CURRENT,
                    summary=self._message(
                        "beat_current",
                        effective_language,
                        title=target_beat.title,
                    ),
                    trigger_action_id=trigger_action_id,
                    reason=self._message(
                        "beat_reason_keeper_selected_next",
                        effective_language,
                    ),
                    consequence_refs=[
                        f"next_beat:{current_beat.beat_id}->{target_beat.beat_id}"
                    ],
                )
            )
            event_text = self._message(
                "keeper_live_control_beat_advanced",
                effective_language,
                title=target_beat.title,
            )
            self._append_keeper_live_control_record(
                session=session,
                operator_id=request.operator_id,
                current_time=current_time,
                language=effective_language,
                target_id=target_beat.beat_id,
                control_type="advance_beat",
                event_text=event_text,
                details={
                    "source_beat_id": current_beat.beat_id,
                    "source_beat_title": current_beat.title,
                    "target_beat_id": target_beat.beat_id,
                    "target_beat_title": target_beat.title,
                    "result": "current",
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_live_control",
                language=effective_language,
            )
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "beat_not_found",
                effective_language,
                beat_id=beat_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="keeper_live_control_beat_not_found",
                    message=message,
                    scope="keeper_live_control_beat",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="keeper_live_control_state_conflict",
                    message=message,
                    scope="keeper_live_control_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "beat_progression_not_available",
                effective_language,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="keeper_live_control_invalid",
                    message=message,
                    scope="keeper_live_control_beat",
                    **error_context,
                )
            ) from exc
        return KeeperLiveControlResponse(
            message=self._message(
                "keeper_live_control_beat_advanced",
                effective_language,
                title=target_beat.title,
            ),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            target_id=target_beat.beat_id,
            target_type="beat",
        )

    def update_keeper_session_lifecycle(
        self,
        session_id: str,
        request: UpdateSessionLifecycleRequest,
    ) -> KeeperLiveControlResponse:
        session, effective_language, error_context = self._load_keeper_lifecycle_context(
            session_id,
            request,
        )
        try:
            current_status = session.status
            allowed_transitions = {
                SessionStatus.PLANNED: {SessionStatus.ACTIVE},
                SessionStatus.ACTIVE: {SessionStatus.PAUSED, SessionStatus.COMPLETED},
                SessionStatus.PAUSED: {SessionStatus.ACTIVE, SessionStatus.COMPLETED},
                SessionStatus.COMPLETED: set(),
            }
            if request.target_status not in allowed_transitions.get(current_status, set()):
                raise ValueError(
                    self._message(
                        "session_status_transition_invalid",
                        effective_language,
                        from_status=self._session_status_label(current_status, effective_language),
                        to_status=self._session_status_label(request.target_status, effective_language),
                    )
                )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version
            session.status = request.target_status
            event_text = self._message(
                "session_status_updated",
                effective_language,
                status=self._session_status_label(request.target_status, effective_language),
            )
            self._append_keeper_live_control_record(
                session=session,
                operator_id=request.operator_id,
                current_time=current_time,
                language=effective_language,
                target_id=session.session_id,
                control_type="session_lifecycle",
                event_text=event_text,
                details={
                    "from_status": current_status.value,
                    "to_status": request.target_status.value,
                    "result": "updated",
                },
            )
            session.state_version += 1
            session.updated_at = current_time
            self._save_session(
                session,
                expected_version=expected_version,
                reason="keeper_live_control",
                language=effective_language,
            )
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="session_lifecycle_state_conflict",
                    message=message,
                    scope="session_lifecycle_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_status_transition_invalid",
                effective_language,
                from_status=self._session_status_label(session.status, effective_language),
                to_status=self._session_status_label(request.target_status, effective_language),
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="session_lifecycle_invalid",
                    message=message,
                    scope="session_lifecycle",
                    **error_context,
                )
            ) from exc
        return KeeperLiveControlResponse(
            message=self._message(
                "session_status_updated",
                effective_language,
                status=self._session_status_label(request.target_status, effective_language),
            ),
            session_id=session.session_id,
            state_version=session.state_version,
            language_preference=effective_language,
            target_id=session.session_id,
            target_type="session_status",
        )

    def review_draft_action(
        self,
        session_id: str,
        draft_id: str,
        request: ReviewDraftRequest,
    ) -> ReviewDraftResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="draft_review_session_not_found",
                    message=message,
                    scope="draft_review_session",
                    session_id=session_id,
                    draft_id=draft_id,
                    reviewer_id=request.reviewer_id,
                )
            ) from exc
        try:
            draft_action = self._get_draft_action(session, draft_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "draft_not_found",
                error_language,
                draft_id=draft_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="draft_review_not_found",
                    message=message,
                    scope="draft_review_draft",
                    session_id=session.session_id,
                    draft_id=draft_id,
                    reviewer_id=request.reviewer_id,
                )
            ) from exc
        if draft_action.review_status != ReviewStatus.PENDING:
            raise ValueError(
                build_session_action_error_detail(
                    code="draft_review_invalid",
                    message=self._message("draft_not_pending", error_language, draft_id=draft_id),
                    scope="draft_review_state",
                    session_id=session.session_id,
                    actor_id=draft_action.actor_id,
                    draft_id=draft_id,
                    reviewer_id=request.reviewer_id,
                )
            )

        effective_language = self._resolve_language(
            request.language_preference,
            draft_action.language_preference,
            session.language_preference,
        )
        error_context = {
            "session_id": session.session_id,
            "actor_id": draft_action.actor_id,
            "draft_id": draft_id,
            "reviewer_id": request.reviewer_id,
        }
        try:
            self._authorize_reviewer(
                session,
                draft_action=draft_action,
                reviewer_id=request.reviewer_id,
                language=effective_language,
            )
            current_time = datetime.now(timezone.utc)
            expected_version = session.state_version

            if request.decision in {
                ReviewDecisionType.APPROVE,
                ReviewDecisionType.EDIT,
                ReviewDecisionType.MANUAL_OVERRIDE,
            }:
                self._ensure_draft_reviewable(
                    session,
                    draft_action=draft_action,
                    decision=request.decision,
                    language=effective_language,
                )
                reviewed_action, authoritative_action = self._finalize_reviewed_action(
                    session=session,
                    draft_action=draft_action,
                    request=request,
                    effective_language=effective_language,
                    current_time=current_time,
                )
                session.state_version += 1
                session.updated_at = current_time
                self._append_audit_log(
                    session,
                    action=AuditActionType.REVIEW_DECISION,
                    actor_id=request.reviewer_id,
                    subject_id=reviewed_action.review_id,
                    current_time=current_time,
                    details={
                        "draft_id": draft_action.draft_id,
                        "decision": request.decision.value,
                        "review_status": reviewed_action.review_status.value,
                        "learn_from_final": reviewed_action.learn_from_final,
                        "editor_notes": request.editor_notes,
                    },
                )
                self._save_session(
                    session,
                    expected_version=expected_version,
                    reason=f"draft_review_{reviewed_action.review_status.value}",
                    language=effective_language,
                )
                return ReviewDraftResponse(
                    message=self._message(
                        "draft_edited"
                        if reviewed_action.review_status == ReviewStatus.EDITED
                        else "draft_approved",
                        effective_language,
                    ),
                    session_id=session.session_id,
                    state_version=session.state_version,
                    language_preference=effective_language,
                    grounding_degraded=self._is_grounding_degraded(
                        session,
                        reviewed_action.rules_grounding,
                    ),
                    reviewed_action=reviewed_action,
                    authoritative_action=authoritative_action,
                )

            if request.decision == ReviewDecisionType.REJECT:
                draft_action.review_status = ReviewStatus.REJECTED
                session.state_version += 1
                session.updated_at = current_time
                self._append_audit_log(
                    session,
                    action=AuditActionType.REVIEW_DECISION,
                    actor_id=request.reviewer_id,
                    subject_id=draft_action.draft_id,
                    current_time=current_time,
                    details={
                        "draft_id": draft_action.draft_id,
                        "decision": request.decision.value,
                        "review_status": ReviewStatus.REJECTED.value,
                        "editor_notes": request.editor_notes,
                    },
                )
                self._save_session(
                    session,
                    expected_version=expected_version,
                    reason="draft_review_rejected",
                    language=effective_language,
                )
                return ReviewDraftResponse(
                    message=self._message("draft_rejected", effective_language),
                    session_id=session.session_id,
                    state_version=session.state_version,
                    language_preference=effective_language,
                    grounding_degraded=self._is_grounding_degraded(
                        session,
                        draft_action.rules_grounding,
                    ),
                )

            if request.decision == ReviewDecisionType.REGENERATE:
                self._ensure_draft_reviewable(
                    session,
                    draft_action=draft_action,
                    decision=request.decision,
                    language=effective_language,
                )
                draft_action.review_status = ReviewStatus.REGENERATED
                regenerated_structured_action = (
                    request.regenerated_structured_action
                    if request.regenerated_structured_action is not None
                    else draft_action.structured_action
                )
                regenerated_effects, regenerated_effect_contract_origin = (
                    self._resolve_action_effect_contract(
                        explicit_effects=request.regenerated_effects,
                        structured_action=regenerated_structured_action,
                        fallback_effects=(
                            None
                            if request.regenerated_structured_action is not None
                            else draft_action.effects
                        ),
                        fallback_effect_contract_origin=(
                            EffectContractOrigin.EXPLICIT
                            if request.regenerated_structured_action is not None
                            else draft_action.effect_contract_origin
                        ),
                    )
                )
                regenerated_rules_grounding = self._ground_rules_for_action(
                    actor_id=draft_action.actor_id,
                    actor_type=draft_action.actor_type,
                    query_text=self._resolve_rules_query_text(
                        (
                            request.regenerated_structured_action or {}
                        ).get("rules_query_text")
                        if request.regenerated_structured_action is not None
                        else draft_action.rules_grounding.query_text
                        if draft_action.rules_grounding is not None
                        else None,
                        request.regenerated_draft_text or draft_action.draft_text,
                        request.regenerated_structured_action
                        if request.regenerated_structured_action is not None
                        else draft_action.structured_action,
                    ),
                    deterministic_resolution_required=(
                        bool(
                            (
                                request.regenerated_structured_action or {}
                            ).get("deterministic_resolution_required")
                        )
                        if request.regenerated_structured_action is not None
                        else (
                            draft_action.rules_grounding.deterministic_resolution_required
                            if draft_action.rules_grounding is not None
                            else False
                        )
                    ),
                )
                self._annotate_grounding_review_summary(
                    session=session,
                    rules_grounding=regenerated_rules_grounding,
                )
                regenerated_draft = self._build_draft_action(
                    session=session,
                    actor_id=draft_action.actor_id,
                    actor_type=draft_action.actor_type,
                    visibility_scope=draft_action.visibility_scope,
                    visible_to=draft_action.visible_to,
                    draft_text=request.regenerated_draft_text or draft_action.draft_text,
                    structured_action=regenerated_structured_action,
                    effects=regenerated_effects,
                    effect_contract_origin=regenerated_effect_contract_origin,
                    rationale_summary=request.editor_notes
                    or self._message("draft_rationale", effective_language),
                    rules_grounding=regenerated_rules_grounding,
                    language=effective_language,
                    behavior_context=(
                        self._get_behavior_context(session, draft_action.actor_id)
                        if draft_action.actor_type == ActorType.INVESTIGATOR
                        else []
                    ),
                    current_time=current_time,
                    supersedes_draft_id=draft_action.draft_id,
                )
                session.draft_actions.append(regenerated_draft)
                session.state_version += 1
                session.updated_at = current_time
                self._append_audit_log(
                    session,
                    action=AuditActionType.REVIEW_DECISION,
                    actor_id=request.reviewer_id,
                    subject_id=draft_action.draft_id,
                    current_time=current_time,
                    details={
                        "decision": request.decision.value,
                        "review_status": ReviewStatus.REGENERATED.value,
                        "replacement_draft_id": regenerated_draft.draft_id,
                    },
                )
                self._append_audit_log(
                    session,
                    action=AuditActionType.DRAFT_CREATED,
                    actor_id=regenerated_draft.actor_id,
                    subject_id=regenerated_draft.draft_id,
                    current_time=current_time,
                    details={
                        "origin": "regenerate",
                        "supersedes_draft_id": draft_action.draft_id,
                        "risk_level": regenerated_draft.risk_level.value,
                    },
                )
                self._save_session(
                    session,
                    expected_version=expected_version,
                    reason="draft_review_regenerated",
                    language=effective_language,
                )
                return ReviewDraftResponse(
                    message=self._message("draft_regenerated", effective_language),
                    session_id=session.session_id,
                    state_version=session.state_version,
                    language_preference=effective_language,
                    grounding_degraded=self._is_grounding_degraded(
                        session,
                        regenerated_draft.rules_grounding,
                    ),
                    regenerated_draft=regenerated_draft,
                )

            raise ValueError(
                self._message(
                    "unsupported_review_decision",
                    effective_language,
                    decision=request.decision.value,
                )
            )
        except PermissionError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "reviewer_not_authorized",
                effective_language,
            )
            raise PermissionError(
                build_session_action_error_detail(
                    code="draft_review_reviewer_not_authorized",
                    message=message,
                    scope="draft_review_permission",
                    **error_context,
                )
            ) from exc
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "draft_stale",
                effective_language,
                draft_id=draft_id,
                current_version=session.state_version,
                created_at_version=draft_action.created_at_version,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="draft_review_conflict",
                    message=message,
                    scope="draft_review_state",
                    **error_context,
                )
            ) from exc
        except ValueError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "unsupported_review_decision",
                effective_language,
                decision=request.decision.value,
            )
            raise ValueError(
                build_session_action_error_detail(
                    code="draft_review_invalid",
                    message=message,
                    scope="draft_review_execution",
                    **error_context,
                )
            ) from exc

    def rollback_session(
        self,
        session_id: str,
        request: RollbackRequest,
    ) -> RollbackResponse:
        error_language = self._resolve_language(request.language_preference)
        try:
            session = self._load_session(session_id, language=error_language)
        except LookupError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "session_not_found",
                error_language,
                session_id=session_id,
            )
            raise LookupError(
                build_session_action_error_detail(
                    code="rollback_session_not_found",
                    message=message,
                    scope="rollback_session",
                    session_id=session_id,
                    target_version=request.target_version,
                )
            ) from exc
        effective_language = self._resolve_language(
            request.language_preference,
            session.language_preference,
        )
        event_text = self._message(
            "rollback_event_detail",
            effective_language,
            from_version=session.state_version,
            to_version=request.target_version,
        )
        try:
            rolled_back_session = self.repository.rollback(
                session_id,
                target_version=request.target_version,
                event_text=event_text,
            )
        except ConflictError as exc:
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                "state_conflict",
                effective_language,
            )
            raise ConflictError(
                build_session_action_error_detail(
                    code="rollback_state_conflict",
                    message=message,
                    scope="rollback_state",
                    session_id=session.session_id,
                    target_version=request.target_version,
                )
            ) from exc
        except ValueError as exc:
            raise ValueError(
                build_session_action_error_detail(
                    code="rollback_snapshot_not_found",
                    message=self._message(
                        "snapshot_not_found",
                        effective_language,
                        version=request.target_version,
                    ),
                    scope="rollback_target",
                    session_id=session.session_id,
                    target_version=request.target_version,
                )
            ) from exc
        current_view = filter_session_for_viewer(
            rolled_back_session,
            viewer_id=None,
            viewer_role=ViewerRole.KEEPER,
        )
        return RollbackResponse(
            message=self._message(
                "session_rolled_back",
                effective_language,
                version=request.target_version,
            ),
            session_id=rolled_back_session.session_id,
            state_version=rolled_back_session.state_version,
            language_preference=effective_language,
            current_view=current_view,
        )

    def _load_session(
        self,
        session_id: str,
        *,
        language: LanguagePreference,
    ) -> SessionState:
        session = self.repository.get(session_id)
        if session is None:
            raise LookupError(self._message("session_not_found", language, session_id=session_id))
        self._sync_beat_statuses(session)
        return session

    def _load_character_import_source(
        self,
        source_id: str,
        *,
        language: LanguagePreference,
    ) -> KnowledgeSourceState:
        if self.knowledge_repository is None:
            raise ValueError(self._message("character_import_not_supported", language))
        source = self.knowledge_repository.get_source(source_id)
        if source is None:
            raise LookupError(
                self._message(
                    "character_import_source_not_found",
                    language,
                    source_id=source_id,
                )
            )
        if source.character_sheet_extraction is None:
            raise ValueError(
                self._message(
                    "character_import_missing_extraction",
                    language,
                    source_id=source_id,
                )
            )
        return source

    def _validate_viewer(
        self,
        session: SessionState,
        *,
        viewer_id: str | None,
        viewer_role: ViewerRole,
        language: LanguagePreference,
    ) -> None:
        if viewer_role == ViewerRole.KEEPER:
            return
        if viewer_id is None:
            raise ValueError(self._message("viewer_id_required", language))
        if viewer_id not in {participant.actor_id for participant in session.participants}:
            raise ValueError(
                self._message("viewer_not_participant", language, viewer_id=viewer_id)
            )

    def _get_participant(
        self,
        session: SessionState,
        actor_id: str,
        *,
        language: LanguagePreference,
    ) -> SessionParticipant:
        participant = self._find_participant(session, actor_id)
        if participant is None:
            raise ValueError(
                self._message("actor_not_participant", language, actor_id=actor_id)
            )
        return participant

    @staticmethod
    def _find_participant(session: SessionState, actor_id: str) -> SessionParticipant | None:
        for participant in session.participants:
            if participant.actor_id == actor_id:
                return participant
        return None

    def _get_draft_action(
        self,
        session: SessionState,
        draft_id: str,
        *,
        language: LanguagePreference,
    ) -> DraftAction:
        for draft_action in session.draft_actions:
            if draft_action.draft_id == draft_id:
                return draft_action
        raise LookupError(self._message("draft_not_found", language, draft_id=draft_id))

    def _get_keeper_prompt(
        self,
        session: SessionState,
        prompt_id: str,
        *,
        language: LanguagePreference,
    ) -> QueuedKPPrompt:
        for prompt in session.progress_state.queued_kp_prompts:
            if prompt.prompt_id == prompt_id:
                return prompt
        raise LookupError(self._message("keeper_prompt_not_found", language, prompt_id=prompt_id))

    # TODO: Replace reviewer_id string checks with actor-bound auth/session tokens in a real auth layer.
    def _authorize_reviewer(
        self,
        session: SessionState,
        *,
        draft_action: DraftAction,
        reviewer_id: str,
        language: LanguagePreference,
    ) -> None:
        if reviewer_id == session.keeper_id:
            return
        if session.allow_test_mode_self_review and reviewer_id == draft_action.actor_id:
            return
        raise PermissionError(self._message("reviewer_not_authorized", language))

    # TODO: Replace operator_id string checks with actor-bound auth/session tokens in a real auth layer.
    def _authorize_operator(
        self,
        session: SessionState,
        *,
        operator_id: str,
        language: LanguagePreference,
        error_detail: dict[str, Any] | None = None,
    ) -> None:
        if operator_id == session.keeper_id:
            return
        raise PermissionError(error_detail or self._message("operator_not_authorized", language))

    def _update_keeper_prompt(
        self,
        prompt: QueuedKPPrompt,
        *,
        status: KeeperPromptStatus | None,
        add_notes: list[str],
        priority: KeeperPromptPriority | None,
        assigned_to: str | None,
        aftermath_label: str | None,
        duration_rounds: int | None,
        current_time: datetime,
        language: LanguagePreference,
    ) -> None:
        if prompt.category != "san_aftermath" and (
            aftermath_label is not None or duration_rounds is not None
        ):
            raise ValueError(
                self._message("keeper_prompt_aftermath_fields_unsupported", language)
            )
        if prompt.category == "san_aftermath":
            if aftermath_label is not None:
                prompt.aftermath_label = aftermath_label.strip()
                prompt.updated_at = current_time
            if duration_rounds is not None:
                prompt.duration_rounds = duration_rounds
                prompt.updated_at = current_time
            if status == KeeperPromptStatus.COMPLETED and (
                not prompt.aftermath_label or prompt.duration_rounds is None
            ):
                raise ValueError(
                    self._message(
                        "san_aftermath_completion_requires_resolution",
                        language,
                    )
                )
        if status is not None:
            self._transition_keeper_prompt(
                prompt,
                status=status,
                current_time=current_time,
                language=language,
            )
        if priority is not None:
            prompt.priority = priority
            prompt.updated_at = current_time
        if assigned_to is not None:
            prompt.assigned_to = assigned_to.strip()
            prompt.updated_at = current_time
        for note in self._normalize_string_list(add_notes):
            if note not in prompt.notes:
                prompt.notes.append(note)
                prompt.updated_at = current_time

    def _transition_keeper_prompt(
        self,
        prompt: QueuedKPPrompt,
        *,
        status: KeeperPromptStatus,
        current_time: datetime,
        language: LanguagePreference,
    ) -> None:
        if prompt.status == status:
            prompt.updated_at = current_time
            return
        if prompt.status in {KeeperPromptStatus.DISMISSED, KeeperPromptStatus.COMPLETED}:
            raise ValueError(
                self._message(
                    "keeper_prompt_terminal",
                    language,
                    prompt_id=prompt.prompt_id,
                )
            )
        allowed_transitions = {
            KeeperPromptStatus.PENDING: {
                KeeperPromptStatus.ACKNOWLEDGED,
                KeeperPromptStatus.DISMISSED,
                KeeperPromptStatus.COMPLETED,
            },
            KeeperPromptStatus.ACKNOWLEDGED: {
                KeeperPromptStatus.DISMISSED,
                KeeperPromptStatus.COMPLETED,
            },
        }
        if status not in allowed_transitions.get(prompt.status, set()):
            raise ValueError(
                self._message(
                    "keeper_prompt_status_invalid",
                    language,
                    from_status=self._kp_prompt_status_label(prompt.status, language),
                    to_status=self._kp_prompt_status_label(status, language),
                )
            )
        prompt.status = status
        prompt.updated_at = current_time
        if status == KeeperPromptStatus.ACKNOWLEDGED:
            prompt.acknowledged_at = current_time
        elif status == KeeperPromptStatus.DISMISSED:
            prompt.dismissed_at = current_time
        elif status == KeeperPromptStatus.COMPLETED:
            prompt.completed_at = current_time

    def _auto_dismiss_scene_bound_pending_prompts(
        self,
        session: SessionState,
        *,
        old_scene_id: str | None,
        new_scene_id: str | None,
        source_action_id: str | None,
        current_time: datetime,
        language: LanguagePreference,
    ) -> list[str]:
        if old_scene_id is None or new_scene_id is None or old_scene_id == new_scene_id:
            return []
        dismissed_prompt_ids: list[str] = []
        for prompt in session.progress_state.queued_kp_prompts:
            if prompt.status != KeeperPromptStatus.PENDING:
                continue
            if prompt.scene_id is None:
                continue
            if prompt.scene_id != old_scene_id or prompt.scene_id == new_scene_id:
                continue
            self._transition_keeper_prompt(
                prompt,
                status=KeeperPromptStatus.DISMISSED,
                current_time=current_time,
                language=language,
            )
            dismissed_prompt_ids.append(prompt.prompt_id)
        if dismissed_prompt_ids:
            self._append_audit_log(
                session,
                action=AuditActionType.KEEPER_PROMPT_UPDATED,
                actor_id=None,
                subject_id=None,
                current_time=current_time,
                details={
                    "reason": "scene_changed",
                    "affected_prompt_ids": dismissed_prompt_ids,
                    "old_scene_id": old_scene_id,
                    "new_scene_id": new_scene_id,
                    "source_action_id": source_action_id,
                },
            )
        return dismissed_prompt_ids

    def _auto_dismiss_expired_pending_prompts(
        self,
        session: SessionState,
        *,
        expired_beat_ids: list[str],
        source_action_id: str | None,
        current_time: datetime,
        language: LanguagePreference,
    ) -> list[str]:
        normalized_expired_beat_ids = list(dict.fromkeys(expired_beat_ids))
        if not normalized_expired_beat_ids:
            return []
        dismissed_prompt_ids: list[str] = []
        expired_beat_id_set = set(normalized_expired_beat_ids)
        for prompt in session.progress_state.queued_kp_prompts:
            if prompt.status != KeeperPromptStatus.PENDING:
                continue
            if prompt.expires_after_beat not in expired_beat_id_set:
                continue
            self._transition_keeper_prompt(
                prompt,
                status=KeeperPromptStatus.DISMISSED,
                current_time=current_time,
                language=language,
            )
            dismissed_prompt_ids.append(prompt.prompt_id)
        if dismissed_prompt_ids:
            self._append_audit_log(
                session,
                action=AuditActionType.KEEPER_PROMPT_UPDATED,
                actor_id=None,
                subject_id=None,
                current_time=current_time,
                details={
                    "reason": "beat_expired",
                    "affected_prompt_ids": dismissed_prompt_ids,
                    "expired_beat_ids": normalized_expired_beat_ids,
                    "source_action_id": source_action_id,
                },
            )
        return dismissed_prompt_ids

    def _ensure_draft_reviewable(
        self,
        session: SessionState,
        *,
        draft_action: DraftAction,
        decision: ReviewDecisionType,
        language: LanguagePreference,
    ) -> None:
        if self._has_superseding_draft(session, draft_action.draft_id):
            raise ConflictError(
                self._message("draft_superseded", language, draft_id=draft_action.draft_id)
            )
        if decision not in {
            ReviewDecisionType.APPROVE,
            ReviewDecisionType.EDIT,
            ReviewDecisionType.MANUAL_OVERRIDE,
        }:
            return
        if session.state_version - draft_action.created_at_version > self.MAX_REVIEW_VERSION_DRIFT:
            raise ConflictError(
                self._message(
                    "draft_stale",
                    language,
                    draft_id=draft_action.draft_id,
                    current_version=session.state_version,
                    created_at_version=draft_action.created_at_version,
                )
            )

    @staticmethod
    def _has_superseding_draft(session: SessionState, draft_id: str) -> bool:
        return any(
            draft.supersedes_draft_id == draft_id
            for draft in session.draft_actions
            if draft.draft_id != draft_id
        )

    @staticmethod
    def _normalize_visible_to(
        *,
        actor_id: str,
        visibility_scope: VisibilityScope,
        visible_to: list[str],
    ) -> list[str]:
        if visibility_scope in {VisibilityScope.PUBLIC, VisibilityScope.SYSTEM_INTERNAL}:
            return []
        return visible_to or [actor_id]

    @staticmethod
    def _resolve_rules_query_text(
        explicit_query_text: str | None,
        action_text: str,
        structured_action: dict[str, Any],
    ) -> str | None:
        if explicit_query_text is not None and explicit_query_text.strip():
            return explicit_query_text.strip()
        fallback_query = structured_action.get("rules_query_text")
        if isinstance(fallback_query, str) and fallback_query.strip():
            return fallback_query.strip()
        query_text = SessionService._build_rules_query_from_term_matches(action_text)
        if query_text is not None:
            return query_text
        stripped_action_text = SessionService._strip_rules_query_filler(action_text)
        if len(stripped_action_text) < 2:
            return None
        stripped_query_text = SessionService._build_rules_query_from_term_matches(stripped_action_text)
        if stripped_query_text is not None:
            return stripped_query_text
        if not SessionService._has_rules_query_fallback_signal(stripped_action_text):
            return None
        return stripped_action_text

    @staticmethod
    def _build_rules_query_from_term_matches(action_text: str) -> str | None:
        term_matches = extract_term_matches(action_text)
        if not term_matches:
            return None
        canonical_terms: list[str] = []
        for match in term_matches:
            if match.canonical_zh not in canonical_terms:
                canonical_terms.append(match.canonical_zh)
        query_text = " ".join(canonical_terms).strip()
        return query_text if len(query_text) >= 2 else None

    @staticmethod
    def _has_rules_query_fallback_signal(action_text: str) -> bool:
        normalized_text = action_text.strip().lower()
        if not normalized_text:
            return False
        return any(signal in normalized_text for signal in SessionService._RULES_QUERY_FALLBACK_SIGNALS)

    @staticmethod
    def _strip_rules_query_filler(action_text: str) -> str:
        stripped = action_text.strip()
        if not stripped:
            return ""
        stripped = re.sub(r"^[，。！？、；：,\.\!\?\s]+", "", stripped)
        prefixes = ("我", "我们", "他", "她", "它", "他们", "她们", "它们", "想", "要", "准备", "打算", "决定", "尝试")
        changed = True
        while stripped and changed:
            changed = False
            for prefix in prefixes:
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix) :].lstrip("，。！？、；：,.!? ")
                    changed = True
        stripped = stripped.strip()
        if stripped in {"好", "好的", "行", "可以", "收到", "明白", "知道了", "嗯", "哦"}:
            return ""
        return stripped

    def _ground_rules_for_action(
        self,
        *,
        actor_id: str,
        actor_type: ActorType,
        query_text: str | None,
        deterministic_resolution_required: bool,
    ) -> RuleGroundingSummary | None:
        if self.knowledge_repository is None or query_text is None:
            return None
        viewer_role = "keeper" if actor_type == ActorType.KEEPER else "investigator"
        viewer_id = None if viewer_role == "keeper" else actor_id
        query_result = KnowledgeRetriever(self.knowledge_repository.list_chunks()).query_rules(
            query_text,
            viewer_role=viewer_role,
            viewer_id=viewer_id,
            deterministic_resolution_required=deterministic_resolution_required,
        )
        return self._to_rule_grounding_summary(query_result)

    def _to_rule_grounding_summary(
        self,
        query_result: RuleQueryResult,
    ) -> RuleGroundingSummary:
        alternate_paths: list[str] = []
        for chunk in query_result.matched_chunks:
            for path in chunk.alternate_paths:
                if path not in alternate_paths:
                    alternate_paths.append(path)
        matched_topics = [chunk.resolved_topic for chunk in query_result.matched_chunks]
        conflict_topics = matched_topics if query_result.conflicts_found else []
        summary = RuleGroundingSummary(
            query_text=query_result.original_query,
            normalized_query=query_result.normalized_query,
            matched_topics=matched_topics,
            core_clue_flag=any(chunk.core_clue_flag for chunk in query_result.matched_chunks),
            alternate_paths=alternate_paths,
            citations=list(query_result.citations),
            deterministic_resolution_required=query_result.deterministic_resolution_required,
            deterministic_handoff_topic=query_result.deterministic_handoff_topic,
            conflicts_found=query_result.conflicts_found,
            conflict_topics=conflict_topics,
            conflict_explanation=query_result.conflict_explanation,
            human_review_recommended=query_result.human_review_recommended,
            human_review_reason=query_result.human_review_reason,
            chinese_answer_draft=query_result.chinese_answer_draft,
        )
        summary.review_summary = self._build_review_summary(summary)
        return summary

    @staticmethod
    def _build_event_payload(
        *,
        structured_action: dict[str, Any],
        rules_grounding: RuleGroundingSummary | None,
    ) -> dict[str, Any]:
        payload = dict(structured_action)
        if rules_grounding is not None:
            payload["rules_grounding"] = rules_grounding.model_dump(mode="json")
        return payload

    @staticmethod
    def _merge_rationale_with_grounding(
        rationale_summary: str,
        *,
        rules_grounding: RuleGroundingSummary | None,
    ) -> str:
        if rules_grounding is None or rules_grounding.review_summary is None:
            return rationale_summary
        return f"{rationale_summary}；{rules_grounding.review_summary}"

    def _build_review_summary(
        self,
        rules_grounding: RuleGroundingSummary | None,
    ) -> str | None:
        if rules_grounding is None:
            return None
        summary_parts: list[str] = []
        if rules_grounding.chinese_answer_draft:
            summary_parts.append(f"规则依据：{rules_grounding.chinese_answer_draft}")
        if rules_grounding.deterministic_handoff_topic:
            summary_parts.append(
                f"确定性交接主题：{rules_grounding.deterministic_handoff_topic}。"
            )
        if rules_grounding.conflict_explanation:
            summary_parts.append(f"冲突提示：{rules_grounding.conflict_explanation}")
        if rules_grounding.human_review_reason:
            summary_parts.append(f"复核建议：{rules_grounding.human_review_reason}")
        if rules_grounding.citations:
            summary_parts.append(f"引用：{'；'.join(rules_grounding.citations)}。")
        if not summary_parts:
            if rules_grounding.review_summary is not None:
                return rules_grounding.review_summary
            return "未命中可用规则依据。"
        return "".join(summary_parts)

    def _build_draft_action(
        self,
        *,
        session: SessionState,
        actor_id: str,
        actor_type: ActorType,
        visibility_scope: VisibilityScope,
        visible_to: list[str],
        draft_text: str,
        structured_action: dict[str, Any],
        effects: ActionEffects | None,
        effect_contract_origin: EffectContractOrigin,
        rationale_summary: str,
        rules_grounding: RuleGroundingSummary | None,
        language: LanguagePreference,
        behavior_context: list[BehaviorPrecedent],
        current_time: datetime,
        supersedes_draft_id: str | None = None,
    ) -> DraftAction:
        classification_structured_action = dict(structured_action)
        resolved_effects = effects or ActionEffects()
        if self._effects_have_content(resolved_effects):
            classification_structured_action["effects"] = resolved_effects.model_dump(mode="json")
        risk_level, core_clue_flag, affects_state, requires_explicit_approval = (
            self._classify_draft_metadata(
                action_text=draft_text,
                structured_action=classification_structured_action,
            )
        )
        if rules_grounding is not None:
            if rules_grounding.core_clue_flag:
                core_clue_flag = True
            if rules_grounding.human_review_recommended or rules_grounding.core_clue_flag:
                requires_explicit_approval = True
        return DraftAction(
            actor_id=actor_id,
            actor_type=actor_type,
            visibility_scope=visibility_scope,
            visible_to=visible_to,
            draft_text=draft_text,
            structured_action=structured_action,
            effects=resolved_effects,
            effect_contract_origin=effect_contract_origin,
            risk_level=risk_level,
            core_clue_flag=core_clue_flag,
            affects_state=affects_state,
            requires_explicit_approval=requires_explicit_approval,
            rationale_summary=self._merge_rationale_with_grounding(
                rationale_summary,
                rules_grounding=rules_grounding,
            ),
            rules_grounding=rules_grounding,
            behavior_context=behavior_context,
            supersedes_draft_id=supersedes_draft_id,
            created_at_version=session.state_version + 1,
            language_preference=language,
            created_at=current_time,
        )

    @staticmethod
    def _effects_have_content(effects: ActionEffects) -> bool:
        return any(
            (
                effects.scene_transitions,
                effects.clue_state_effects,
                effects.character_stat_effects,
                effects.inventory_effects,
                effects.visibility_effects,
                effects.status_effects,
            )
        )

    def _finalize_reviewed_action(
        self,
        *,
        session: SessionState,
        draft_action: DraftAction,
        request: ReviewDraftRequest,
        effective_language: LanguagePreference,
        current_time: datetime,
    ) -> tuple[ReviewedAction, AuthoritativeAction]:
        final_text = draft_action.draft_text
        final_structured_action = draft_action.structured_action
        final_effects = draft_action.effects
        final_effect_contract_origin = draft_action.effect_contract_origin
        review_status = ReviewStatus.APPROVED

        if request.decision in {ReviewDecisionType.EDIT, ReviewDecisionType.MANUAL_OVERRIDE}:
            final_text = request.final_text or draft_action.draft_text
            final_structured_action = (
                request.final_structured_action
                if request.final_structured_action is not None
                else draft_action.structured_action
            )
            final_effects, final_effect_contract_origin = self._resolve_action_effect_contract(
                explicit_effects=request.final_effects,
                structured_action=final_structured_action,
                fallback_effects=draft_action.effects,
                fallback_effect_contract_origin=draft_action.effect_contract_origin,
            )
            review_status = ReviewStatus.EDITED

        draft_action.review_status = review_status
        decision = ReviewDecision(
            decision=request.decision,
            editor_notes=request.editor_notes,
            approved_by=request.reviewer_id,
            approved_at=current_time,
        )
        reviewed_action = ReviewedAction(
            draft_id=draft_action.draft_id,
            actor_id=draft_action.actor_id,
            actor_type=draft_action.actor_type,
            visibility_scope=draft_action.visibility_scope,
            visible_to=draft_action.visible_to,
            review_status=review_status,
            final_text=final_text,
            final_structured_action=final_structured_action,
            effects=final_effects,
            effect_contract_origin=final_effect_contract_origin,
            rules_grounding=draft_action.rules_grounding,
            review_summary=self._build_review_summary(draft_action.rules_grounding),
            learn_from_final=request.learn_from_final,
            decision=decision,
            language_preference=effective_language,
            created_at=current_time,
        )
        authoritative_action = self._build_authoritative_action(
            source_type=AuthoritativeActionSource.REVIEWED_DRAFT,
            actor_id=draft_action.actor_id,
            actor_type=draft_action.actor_type,
            visibility_scope=draft_action.visibility_scope,
            visible_to=draft_action.visible_to,
            text=final_text,
            structured_action=final_structured_action,
            effects=final_effects,
            effect_contract_origin=final_effect_contract_origin,
            rules_grounding=draft_action.rules_grounding,
            language_preference=effective_language,
            created_at=current_time,
            draft_id=draft_action.draft_id,
            review_id=reviewed_action.review_id,
            review_summary=reviewed_action.review_summary,
        )
        canonical_event = self._apply_authoritative_action(
            session=session,
            authoritative_action=authoritative_action,
            event_type=EventType.REVIEWED_ACTION,
            language=effective_language,
            current_time=current_time,
        )
        reviewed_action.execution_summary = authoritative_action.execution_summary
        reviewed_action.applied_effects = list(authoritative_action.applied_effects)
        reviewed_action.applied_beat_transitions = list(authoritative_action.applied_beat_transitions)
        reviewed_action.applied_state_changes = [
            *[effect.summary for effect in authoritative_action.applied_effects],
            *[transition.summary for transition in authoritative_action.applied_beat_transitions],
        ]
        reviewed_action.authoritative_action_id = authoritative_action.action_id
        draft_action.review_status = review_status
        authoritative_action.review_id = reviewed_action.review_id
        canonical_event.structured_payload.update(
            {
                "review_id": reviewed_action.review_id,
                "draft_id": draft_action.draft_id,
                "review_status": review_status.value,
                "final_structured_action": final_structured_action,
                "learn_from_final": reviewed_action.learn_from_final,
                "review_summary": reviewed_action.review_summary,
                "execution_summary": reviewed_action.execution_summary,
                "applied_state_changes": reviewed_action.applied_state_changes,
                "applied_beat_transitions": [
                    transition.model_dump(mode="json")
                    for transition in reviewed_action.applied_beat_transitions
                ],
            }
        )
        reviewed_action.canonical_event_id = canonical_event.event_id
        session.reviewed_actions.append(reviewed_action)
        self._update_behavior_memory(session, reviewed_action, current_time)
        return reviewed_action, authoritative_action

    def _build_authoritative_action(
        self,
        *,
        source_type: AuthoritativeActionSource,
        actor_id: str,
        actor_type: ActorType,
        visibility_scope: VisibilityScope,
        visible_to: list[str],
        text: str,
        structured_action: dict[str, Any],
        effects: ActionEffects,
        effect_contract_origin: EffectContractOrigin,
        rules_grounding: RuleGroundingSummary | None,
        language_preference: LanguagePreference,
        created_at: datetime,
        draft_id: str | None = None,
        review_id: str | None = None,
        review_summary: str | None = None,
    ) -> AuthoritativeAction:
        return AuthoritativeAction(
            source_type=source_type,
            actor_id=actor_id,
            actor_type=actor_type,
            visibility_scope=visibility_scope,
            visible_to=visible_to,
            text=text,
            structured_action=structured_action,
            effects=effects.model_copy(deep=True),
            effect_contract_origin=effect_contract_origin,
            rules_grounding=rules_grounding,
            review_summary=review_summary,
            draft_id=draft_id,
            review_id=review_id,
            language_preference=language_preference,
            created_at=created_at,
        )

    @classmethod
    def _resolve_action_effect_contract(
        cls,
        *,
        explicit_effects: ActionEffects | None,
        structured_action: dict[str, Any],
        fallback_effects: ActionEffects | None = None,
        fallback_effect_contract_origin: EffectContractOrigin = EffectContractOrigin.EXPLICIT,
    ) -> tuple[ActionEffects, EffectContractOrigin]:
        if explicit_effects is not None:
            return explicit_effects.model_copy(deep=True), EffectContractOrigin.EXPLICIT
        if cls._has_legacy_effect_payload(structured_action):
            return (
                cls._legacy_effects_from_structured_action(structured_action),
                EffectContractOrigin.LEGACY_STRUCTURED_ACTION,
            )
        if fallback_effects is not None:
            return fallback_effects.model_copy(deep=True), fallback_effect_contract_origin
        return ActionEffects(), EffectContractOrigin.EXPLICIT

    @classmethod
    def _legacy_effects_from_structured_action(
        cls, structured_action: dict[str, Any]
    ) -> ActionEffects:
        effects = ActionEffects()
        scene_transition = structured_action.get("scene_transition")
        if isinstance(scene_transition, str) and scene_transition.strip():
            effects.scene_transitions.append(SceneTransitionEffect(title=scene_transition.strip()))
        elif isinstance(scene_transition, dict):
            effects.scene_transitions.append(SceneTransitionEffect.model_validate(scene_transition))

        for clue_update in cls._normalize_effect_list(structured_action.get("clue_updates")):
            effects.clue_state_effects.append(ClueStateEffect.model_validate(clue_update))

        for visibility_effect in cls._normalize_effect_list(structured_action.get("visibility_effects")):
            effects.visibility_effects.append(VisibilityEffect.model_validate(visibility_effect))

        for character_update in cls._normalize_effect_list(structured_action.get("character_updates")):
            actor_id = character_update.get("actor_id")
            if not isinstance(actor_id, str) or not actor_id.strip():
                continue
            effect_actor_id = actor_id.strip()
            stat_payload = {
                "actor_id": effect_actor_id,
                "current_hit_points": character_update.get("current_hit_points"),
                "current_magic_points": character_update.get("current_magic_points"),
                "current_sanity": character_update.get("current_sanity"),
                "hp_delta": character_update.get("hp_delta"),
                "mp_delta": character_update.get("mp_delta"),
                "san_delta": character_update.get("san_delta"),
            }
            if any(value is not None for key, value in stat_payload.items() if key != "actor_id"):
                effects.character_stat_effects.append(CharacterStatEffect.model_validate(stat_payload))

            inventory_payload = {
                "actor_id": effect_actor_id,
                "add_items": cls._normalize_string_list(character_update.get("add_inventory")),
                "remove_items": cls._normalize_string_list(character_update.get("remove_inventory")),
            }
            if inventory_payload["add_items"] or inventory_payload["remove_items"]:
                effects.inventory_effects.append(InventoryEffect.model_validate(inventory_payload))

            status_payload = {
                "actor_id": effect_actor_id,
                "add_status_effects": cls._normalize_string_list(
                    character_update.get("add_status_effects")
                ),
                "remove_status_effects": cls._normalize_string_list(
                    character_update.get("remove_status_effects")
                ),
                "add_temporary_conditions": cls._normalize_string_list(
                    character_update.get("add_temporary_conditions")
                ),
                "remove_temporary_conditions": cls._normalize_string_list(
                    character_update.get("remove_temporary_conditions")
                ),
                "add_private_notes": cls._normalize_string_list(
                    character_update.get("add_private_notes")
                ),
                "remove_private_notes": cls._normalize_string_list(
                    character_update.get("remove_private_notes")
                ),
                "add_secret_state_refs": cls._normalize_string_list(
                    character_update.get("add_secret_state_refs")
                ),
                "remove_secret_state_refs": cls._normalize_string_list(
                    character_update.get("remove_secret_state_refs")
                ),
            }
            if any(value for key, value in status_payload.items() if key != "actor_id"):
                effects.status_effects.append(StatusEffect.model_validate(status_payload))
        return effects

    @staticmethod
    def _has_legacy_effect_payload(structured_action: dict[str, Any]) -> bool:
        return any(
            key in structured_action
            for key in (
                "scene_transition",
                "clue_updates",
                "visibility_effects",
                "character_updates",
            )
        )

    @staticmethod
    def _normalize_effect_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _merge_action_effects(base_effects: ActionEffects, extra_effects: ActionEffects) -> ActionEffects:
        merged = base_effects.model_copy(deep=True)
        merged.scene_transitions.extend(extra_effects.scene_transitions)
        merged.clue_state_effects.extend(extra_effects.clue_state_effects)
        merged.character_stat_effects.extend(extra_effects.character_stat_effects)
        merged.inventory_effects.extend(extra_effects.inventory_effects)
        merged.visibility_effects.extend(extra_effects.visibility_effects)
        merged.status_effects.extend(extra_effects.status_effects)
        return merged

    def _apply_authoritative_action(
        self,
        *,
        session: SessionState,
        authoritative_action: AuthoritativeAction,
        event_type: EventType,
        language: LanguagePreference,
        current_time: datetime,
    ) -> SessionEvent:
        self._ensure_handoff_matches(authoritative_action, language=language)
        authoritative_action.effects = self._expand_effects_with_triggers(
            session=session,
            authoritative_action=authoritative_action,
        )

        applied_effects: list[AppliedEffectRecord] = []
        for effect in authoritative_action.effects.scene_transitions:
            applied_effects.append(
                self._apply_scene_transition_effect(
                    session=session,
                    effect=effect,
                    source_action_id=authoritative_action.action_id,
                    current_time=current_time,
                    language=language,
                )
            )
        for effect in authoritative_action.effects.clue_state_effects:
            applied_effects.append(
                self._apply_clue_state_effect(
                    session=session,
                    effect=effect,
                    language=language,
                    current_time=current_time,
                )
            )
        for effect in authoritative_action.effects.visibility_effects:
            applied_effects.append(
                self._apply_visibility_effect(
                    session=session,
                    effect=effect,
                    language=language,
                    current_time=current_time,
                )
            )
        for effect in authoritative_action.effects.character_stat_effects:
            applied_effects.append(
                self._apply_character_stat_effect(
                    session=session,
                    effect=effect,
                    language=language,
                    current_time=current_time,
                )
            )
        for effect in authoritative_action.effects.inventory_effects:
            applied_effects.append(
                self._apply_inventory_effect(
                    session=session,
                    effect=effect,
                    language=language,
                    current_time=current_time,
                )
            )
        for effect in authoritative_action.effects.status_effects:
            applied_effects.append(
                self._apply_status_effect(
                    session=session,
                    effect=effect,
                    language=language,
                    current_time=current_time,
                )
            )

        authoritative_action.applied_effects = applied_effects
        authoritative_action.applied_beat_transitions = self._update_scenario_progress(
            session=session,
            authoritative_action=authoritative_action,
            applied_effects=applied_effects,
            current_time=current_time,
            language=language,
        )
        execution_records = [
            *[record.summary for record in applied_effects],
            *[transition.summary for transition in authoritative_action.applied_beat_transitions],
        ]
        authoritative_action.execution_summary = "；".join(execution_records) if execution_records else None
        canonical_event = SessionEvent(
            event_type=event_type,
            actor_id=authoritative_action.actor_id,
            actor_type=authoritative_action.actor_type,
            visibility_scope=authoritative_action.visibility_scope,
            visible_to=authoritative_action.visible_to,
            text=authoritative_action.text,
            structured_payload=self._build_authoritative_event_payload(authoritative_action),
            rules_grounding=authoritative_action.rules_grounding,
            is_authoritative=True,
            language_preference=authoritative_action.language_preference,
            created_at=current_time,
        )
        authoritative_action.canonical_event_id = canonical_event.event_id
        session.authoritative_actions.append(authoritative_action)
        session.timeline.append(canonical_event)
        return canonical_event

    @staticmethod
    def _build_authoritative_event_payload(
        authoritative_action: AuthoritativeAction,
    ) -> dict[str, Any]:
        payload = dict(authoritative_action.structured_action)
        payload.update(
            {
                "authoritative_action_id": authoritative_action.action_id,
                "source_type": authoritative_action.source_type.value,
                "effects": authoritative_action.effects.model_dump(mode="json"),
                "effect_contract_origin": authoritative_action.effect_contract_origin.value,
                "applied_effects": [
                    effect.model_dump(mode="json") for effect in authoritative_action.applied_effects
                ],
                "applied_beat_transitions": [
                    transition.model_dump(mode="json")
                    for transition in authoritative_action.applied_beat_transitions
                ],
                "execution_summary": authoritative_action.execution_summary,
            }
        )
        if authoritative_action.review_id is not None:
            payload["review_id"] = authoritative_action.review_id
        if authoritative_action.draft_id is not None:
            payload["draft_id"] = authoritative_action.draft_id
        if authoritative_action.review_summary is not None:
            payload["review_summary"] = authoritative_action.review_summary
        if authoritative_action.rules_grounding is not None:
            payload["rules_grounding"] = authoritative_action.rules_grounding.model_dump(mode="json")
        return payload

    def _expand_effects_with_triggers(
        self,
        *,
        session: SessionState,
        authoritative_action: AuthoritativeAction,
    ) -> ActionEffects:
        trigger_effects = self._build_trigger_effects(
            session=session,
            authoritative_action=authoritative_action,
        )
        return self._merge_action_effects(authoritative_action.effects, trigger_effects)

    def _build_trigger_effects(
        self,
        *,
        session: SessionState,
        authoritative_action: AuthoritativeAction,
    ) -> ActionEffects:
        action_type = authoritative_action.structured_action.get("type")
        if not isinstance(action_type, str) or not action_type.strip():
            return ActionEffects()

        trigger_effects = ActionEffects()
        existing_targets: set[str] = set()
        for effect in authoritative_action.effects.clue_state_effects:
            if effect.clue_id:
                existing_targets.add(effect.clue_id)
            if effect.clue_title:
                existing_targets.add(effect.clue_title)
        for clue in session.scenario.clues:
            if clue.clue_id in existing_targets or clue.title in existing_targets:
                continue
            for trigger in clue.discovery_triggers:
                if not self._action_matches_trigger(
                    action_type=action_type,
                    required_topic=trigger.required_topic,
                    rules_grounding=authoritative_action.rules_grounding,
                    trigger_action_types=trigger.action_types,
                ):
                    continue
                trigger_effects.clue_state_effects.append(
                    self._build_triggered_clue_effect(
                        clue=clue,
                        actor_id=authoritative_action.actor_id,
                        status=trigger.status_on_discovery,
                        reveal_to_party=trigger.reveal_to.value == "party",
                        assign_to_actor=trigger.assign_to_actor,
                        discovered_via=trigger.discovered_via or action_type.strip(),
                    )
                )
                existing_targets.add(clue.clue_id)
                existing_targets.add(clue.title)
                break
            if clue.clue_id in existing_targets or clue.title in existing_targets:
                continue
            for trigger in clue.fail_forward_triggers:
                if not self._action_matches_trigger(
                    action_type=action_type,
                    required_topic=trigger.required_topic,
                    rules_grounding=authoritative_action.rules_grounding,
                    trigger_action_types=trigger.action_types,
                ):
                    continue
                trigger_effects.clue_state_effects.append(
                    self._build_triggered_clue_effect(
                        clue=clue,
                        actor_id=authoritative_action.actor_id,
                        status=trigger.fallback_status,
                        reveal_to_party=trigger.reveal_to.value == "party",
                        assign_to_actor=trigger.assign_to_actor,
                        discovered_via=trigger.discovered_via,
                        activate_fail_forward=True,
                    )
                )
                existing_targets.add(clue.clue_id)
                existing_targets.add(clue.title)
                break
        return trigger_effects

    @staticmethod
    def _build_triggered_clue_effect(
        *,
        clue: ScenarioClue,
        actor_id: str,
        status: ClueProgressState,
        reveal_to_party: bool,
        assign_to_actor: bool,
        discovered_via: str,
        activate_fail_forward: bool = False,
    ) -> ClueStateEffect:
        if reveal_to_party:
            return ClueStateEffect(
                clue_id=clue.clue_id,
                status=status,
                add_discovered_by=[actor_id],
                add_owner_actor_ids=[actor_id] if assign_to_actor else [],
                discovered_via=discovered_via,
                share_with_party=True,
                activate_fail_forward=activate_fail_forward,
            )
        return ClueStateEffect(
            clue_id=clue.clue_id,
            status=status,
            add_discovered_by=[actor_id],
            add_owner_actor_ids=[actor_id] if assign_to_actor else [],
            discovered_via=discovered_via,
            private_to_actor_ids=[actor_id],
            activate_fail_forward=activate_fail_forward,
        )

    def _action_matches_trigger(
        self,
        *,
        action_type: str,
        required_topic: str | None,
        rules_grounding: RuleGroundingSummary | None,
        trigger_action_types: list[str],
    ) -> bool:
        normalized_action_type = action_type.strip().lower()
        normalized_trigger_types = [item.strip().lower() for item in trigger_action_types if item.strip()]
        if normalized_trigger_types and normalized_action_type not in normalized_trigger_types:
            return False
        return self._rules_topic_matches(required_topic, rules_grounding)

    @staticmethod
    def _rules_topic_matches(
        required_topic: str | None,
        rules_grounding: RuleGroundingSummary | None,
    ) -> bool:
        if required_topic is None:
            return True
        if rules_grounding is None:
            return False
        if rules_grounding.deterministic_handoff_topic == required_topic:
            return True
        return required_topic in rules_grounding.matched_topics

    def _ensure_handoff_matches(
        self,
        authoritative_action: AuthoritativeAction,
        *,
        language: LanguagePreference,
    ) -> None:
        required_handoff_topic = authoritative_action.structured_action.get("required_handoff_topic")
        if not isinstance(required_handoff_topic, str) or not required_handoff_topic.strip():
            return
        actual_topic = (
            authoritative_action.rules_grounding.deterministic_handoff_topic
            if authoritative_action.rules_grounding is not None
            else None
        )
        if actual_topic != required_handoff_topic:
            raise ValueError(
                self._message(
                    "execution_handoff_mismatch",
                    language,
                    expected_topic=required_handoff_topic,
                    actual_topic=actual_topic or "none",
                )
            )

    def _update_scenario_progress(
        self,
        *,
        session: SessionState,
        authoritative_action: AuthoritativeAction,
        applied_effects: list[AppliedEffectRecord],
        current_time: datetime,
        language: LanguagePreference,
    ) -> list[ScenarioBeatTransitionRecord]:
        beats = session.scenario.beats
        if not beats:
            return []

        progress_state = session.progress_state
        transitions = self._collect_fail_forward_progress_transitions(
            session=session,
            authoritative_action=authoritative_action,
            language=language,
        )
        for beat in beats:
            if beat.beat_id in progress_state.completed_beats:
                continue
            block_condition = beat.block_conditions or beat.block_when
            unlock_condition = beat.unlock_conditions or beat.unlock_when
            complete_condition = beat.complete_conditions or beat.complete_when
            if block_condition and self._evaluate_beat_condition(
                session=session,
                authoritative_action=authoritative_action,
                condition=block_condition,
                language=language,
            ):
                if beat.beat_id not in progress_state.blocked_beats:
                    progress_state.blocked_beats.append(beat.beat_id)
                    while beat.beat_id in progress_state.unlocked_beats:
                        progress_state.unlocked_beats.remove(beat.beat_id)
                    transitions.append(
                        self._build_beat_transition_record(
                            beat=beat,
                            transition=ScenarioBeatTransitionType.BLOCKED,
                            summary=self._message("beat_blocked", language, title=beat.title),
                            trigger_action_id=authoritative_action.action_id,
                            reason=self._message("beat_reason_block_conditions_met", language),
                            condition_refs=self._describe_beat_condition(block_condition),
                        )
                    )
                continue

            if beat.beat_id in progress_state.blocked_beats:
                continue
            if beat.beat_id not in progress_state.unlocked_beats and unlock_condition is not None:
                if self._beat_required_clues_satisfied(session, beat) and self._evaluate_beat_condition(
                    session=session,
                    authoritative_action=authoritative_action,
                    condition=unlock_condition,
                    language=language,
                ):
                    progress_state.unlocked_beats.append(beat.beat_id)
                    transitions.append(
                        self._build_beat_transition_record(
                            beat=beat,
                            transition=ScenarioBeatTransitionType.UNLOCKED,
                            summary=self._message("beat_unlocked", language, title=beat.title),
                            trigger_action_id=authoritative_action.action_id,
                            reason=self._message("beat_reason_unlock_conditions_met", language),
                            condition_refs=self._describe_beat_condition(unlock_condition),
                        )
                    )

            if complete_condition is None or beat.beat_id in progress_state.completed_beats:
                continue
            if beat.beat_id not in progress_state.unlocked_beats and progress_state.current_beat != beat.beat_id:
                continue
            if not self._beat_required_clues_satisfied(session, beat):
                continue
            if self._evaluate_beat_condition(
                session=session,
                authoritative_action=authoritative_action,
                condition=complete_condition,
                language=language,
            ):
                progress_state.completed_beats.append(beat.beat_id)
                while beat.beat_id in progress_state.unlocked_beats:
                    progress_state.unlocked_beats.remove(beat.beat_id)
                consequence_refs = self._describe_declared_consequences(beat)
                transitions.append(
                    self._build_beat_transition_record(
                        beat=beat,
                        transition=ScenarioBeatTransitionType.COMPLETED,
                        summary=self._message("beat_completed", language, title=beat.title),
                        trigger_action_id=authoritative_action.action_id,
                        reason=self._message("beat_reason_complete_conditions_met", language),
                        condition_refs=self._describe_beat_condition(complete_condition),
                        consequence_refs=consequence_refs,
                    )
                )
                transitions.extend(
                    self._apply_beat_completion_consequences(
                        session=session,
                        source_beat=beat,
                        authoritative_action=authoritative_action,
                        applied_effects=applied_effects,
                        current_time=current_time,
                        language=language,
                    )
                )

        current_transition, passed_beat_ids = self._refresh_current_beat(
            session,
            trigger_action_id=authoritative_action.action_id,
            language=language,
        )
        if current_transition is not None:
            transitions.append(current_transition)
        expired_prompt_beat_ids = [
            transition.beat_id
            for transition in transitions
            if transition.transition == ScenarioBeatTransitionType.COMPLETED
        ]
        expired_prompt_beat_ids.extend(passed_beat_ids)
        self._auto_dismiss_expired_pending_prompts(
            session,
            expired_beat_ids=expired_prompt_beat_ids,
            source_action_id=authoritative_action.action_id,
            current_time=current_time,
            language=language,
        )
        self._sync_beat_statuses(session)
        if transitions:
            progress_state.last_updated_at = current_time
            progress_state.transition_history.extend(
                [transition.model_copy(deep=True) for transition in transitions]
            )
        return transitions

    def _collect_fail_forward_progress_transitions(
        self,
        *,
        session: SessionState,
        authoritative_action: AuthoritativeAction,
        language: LanguagePreference,
    ) -> list[ScenarioBeatTransitionRecord]:
        transitions: list[ScenarioBeatTransitionRecord] = []
        for effect in authoritative_action.effects.clue_state_effects:
            if not effect.activate_fail_forward:
                continue
            clue = self._find_clue(
                session,
                clue_id=effect.clue_id,
                clue_title=effect.clue_title,
            )
            if clue is None:
                continue
            transitions.extend(
                self._activate_fail_forward_for_clue(
                    session=session,
                    clue=clue,
                    trigger_action_id=authoritative_action.action_id,
                    language=language,
                )
            )
        return transitions

    def _apply_beat_completion_consequences(
        self,
        *,
        session: SessionState,
        source_beat: ScenarioBeat,
        authoritative_action: AuthoritativeAction,
        applied_effects: list[AppliedEffectRecord],
        current_time: datetime,
        language: LanguagePreference,
    ) -> list[ScenarioBeatTransitionRecord]:
        progress_state = session.progress_state
        transitions: list[ScenarioBeatTransitionRecord] = []
        unlock_beat_ids = list(source_beat.next_beats)
        block_beat_ids: list[str] = []
        fail_forward_refs: list[str] = []
        for consequence in source_beat.consequences:
            unlock_beat_ids.extend(consequence.unlock_beat_ids)
            block_beat_ids.extend(consequence.block_beat_ids)
            fail_forward_refs.extend(consequence.activate_fail_forward_for_clue_ids)
            for reveal_clue in consequence.reveal_clues:
                applied_effects.append(
                    self._apply_clue_state_effect(
                        session=session,
                        effect=ClueStateEffect(
                            clue_id=reveal_clue.clue_id,
                            clue_title=reveal_clue.clue_title,
                            status=reveal_clue.status,
                            share_with_party=reveal_clue.share_with_party,
                            private_to_actor_ids=list(reveal_clue.visible_to_actor_ids),
                            add_owner_actor_ids=list(reveal_clue.owner_actor_ids),
                            add_discovered_by=list(reveal_clue.discovered_by_actor_ids),
                            discovered_via=reveal_clue.discovered_via or f"beat:{source_beat.beat_id}",
                        ),
                        language=language,
                        current_time=current_time,
                    )
                )
            for status_update in consequence.apply_statuses:
                applied_effects.append(
                    self._apply_status_effect(
                        session=session,
                        effect=StatusEffect(
                            actor_id=status_update.actor_id,
                            add_status_effects=list(status_update.add_status_effects),
                            add_temporary_conditions=list(status_update.add_temporary_conditions),
                        ),
                        language=language,
                        current_time=current_time,
                    )
                )
            for note_update in consequence.grant_private_notes:
                applied_effects.append(
                    self._apply_status_effect(
                        session=session,
                        effect=StatusEffect(
                            actor_id=note_update.actor_id,
                            add_private_notes=[note_update.note],
                        ),
                        language=language,
                        current_time=current_time,
                    )
                )
            for revealed_scene in consequence.reveal_scenes:
                scene_ref = revealed_scene.scene_id or revealed_scene.scene_ref
                if scene_ref is None:
                    continue
                scene = self._find_scenario_scene(
                    session.scenario.scenes,
                    scene_id=scene_ref,
                    scene_title=scene_ref,
                )
                if scene is None:
                    continue
                if scene.scene_id in progress_state.revealed_scene_refs:
                    continue
                self._reveal_scene_registry_entry(
                    session=session,
                    scene=scene,
                    source_action_id=authoritative_action.action_id,
                    trigger_reason=self._message(
                        "scene_revealed_by_beat",
                        language,
                        title=source_beat.title,
                    ),
                    current_time=current_time,
                )
                applied_effects.append(
                    AppliedEffectRecord(
                        effect_type="beat_consequence.reveal_scene",
                        target_ref=scene.scene_id,
                        summary=self._message(
                            "execution_scene_revealed",
                            language,
                            scene_ref=scene.scene_id,
                        ),
                    )
                )
            for npc_update in consequence.npc_attitude_updates:
                progress_state.npc_attitudes[npc_update.npc_id] = npc_update.attitude
                applied_effects.append(
                    AppliedEffectRecord(
                        effect_type="beat_consequence.npc_attitude",
                        target_ref=npc_update.npc_id,
                        summary=self._message(
                            "execution_npc_attitude_updated",
                            language,
                            npc_id=npc_update.npc_id,
                            attitude=npc_update.attitude,
                        ),
                    )
                )
            for kp_prompt in consequence.queue_kp_prompts:
                assigned_to = (
                    kp_prompt.assigned_to.strip() if kp_prompt.assigned_to is not None else ""
                )
                if not assigned_to:
                    assigned_to = session.keeper_id
                progress_state.queued_kp_prompts.append(
                    QueuedKPPrompt(
                        prompt_text=kp_prompt.prompt_text,
                        beat_id=source_beat.beat_id,
                        scene_id=kp_prompt.scene_id or session.current_scene.scene_id,
                        source_action_id=authoritative_action.action_id,
                        category=kp_prompt.category,
                        priority=kp_prompt.priority,
                        assigned_to=assigned_to,
                        expires_after_beat=kp_prompt.expires_after_beat,
                        notes=[],
                        status=KeeperPromptStatus.PENDING,
                        trigger_reason=kp_prompt.reason
                        or self._message("kp_prompt_triggered_by_beat", language, title=source_beat.title),
                        created_at=current_time,
                        updated_at=current_time,
                    )
                )
                applied_effects.append(
                    AppliedEffectRecord(
                        effect_type="beat_consequence.kp_prompt",
                        target_ref=source_beat.beat_id,
                        summary=self._message(
                            "execution_kp_prompt_queued",
                            language,
                            title=source_beat.title,
                        ),
                    )
                )
            for objective_mark in consequence.mark_scene_objectives_complete:
                target_beat = source_beat
                if objective_mark.beat_id is not None:
                    target_beat = self._find_beat(session.scenario.beats, objective_mark.beat_id)
                completed_objectives = self._mark_scene_objective_complete(
                    session=session,
                    source_action_id=authoritative_action.action_id,
                    beat_id=target_beat.beat_id if objective_mark.beat_id is not None else source_beat.beat_id,
                    scene_id=objective_mark.scene_id,
                    objective_id=objective_mark.objective_id,
                    objective_label=objective_mark.objective_label
                    or target_beat.scene_objective
                    or target_beat.title,
                    trigger_reason=self._message(
                        "scene_objective_completed_by_beat",
                        language,
                        title=source_beat.title,
                    ),
                    current_time=current_time,
                )
                for objective_label in completed_objectives:
                    applied_effects.append(
                        AppliedEffectRecord(
                            effect_type="beat_consequence.scene_objective",
                            target_ref=target_beat.beat_id,
                            summary=self._message(
                                "execution_scene_objective_completed",
                                language,
                                objective=objective_label,
                            ),
                        )
                    )

        for target_beat_id in dict.fromkeys(unlock_beat_ids):
            if (
                target_beat_id in progress_state.unlocked_beats
                or target_beat_id in progress_state.completed_beats
                or target_beat_id in progress_state.blocked_beats
            ):
                continue
            target_beat = self._find_beat(session.scenario.beats, target_beat_id)
            progress_state.unlocked_beats.append(target_beat_id)
            transitions.append(
                self._build_beat_transition_record(
                    beat=target_beat,
                    transition=ScenarioBeatTransitionType.UNLOCKED,
                    summary=self._message("beat_unlocked", language, title=target_beat.title),
                    trigger_action_id=authoritative_action.action_id,
                    reason=self._message(
                        "beat_reason_followup_unlocked",
                        language,
                        title=source_beat.title,
                    ),
                    consequence_refs=self._describe_followup_unlock(source_beat, target_beat_id),
                )
            )

        for target_beat_id in dict.fromkeys(block_beat_ids):
            if target_beat_id in progress_state.completed_beats or target_beat_id in progress_state.blocked_beats:
                continue
            target_beat = self._find_beat(session.scenario.beats, target_beat_id)
            progress_state.blocked_beats.append(target_beat_id)
            while target_beat_id in progress_state.unlocked_beats:
                progress_state.unlocked_beats.remove(target_beat_id)
            if progress_state.current_beat == target_beat_id:
                progress_state.current_beat = None
            transitions.append(
                self._build_beat_transition_record(
                    beat=target_beat,
                    transition=ScenarioBeatTransitionType.BLOCKED,
                    summary=self._message("beat_blocked", language, title=target_beat.title),
                    trigger_action_id=authoritative_action.action_id,
                    reason=self._message(
                        "beat_reason_followup_blocked",
                        language,
                        title=source_beat.title,
                    ),
                    consequence_refs=[f"block_beat:{target_beat_id}"],
                )
            )

        for clue_ref in dict.fromkeys(fail_forward_refs):
            clue = self._find_clue(session, clue_id=clue_ref, clue_title=clue_ref)
            if clue is None:
                continue
            transitions.extend(
                self._activate_fail_forward_for_clue(
                    session=session,
                    clue=clue,
                    trigger_action_id=authoritative_action.action_id,
                    language=language,
                    fallback_beat=source_beat,
                )
            )
        return transitions

    def _evaluate_beat_condition(
        self,
        *,
        session: SessionState,
        authoritative_action: AuthoritativeAction,
        condition: BeatCondition,
        language: LanguagePreference,
    ) -> bool:
        if condition.all_of:
            return all(
                self._evaluate_beat_condition(
                    session=session,
                    authoritative_action=authoritative_action,
                    condition=nested,
                    language=language,
                )
                for nested in condition.all_of
            )
        if condition.any_of:
            return any(
                self._evaluate_beat_condition(
                    session=session,
                    authoritative_action=authoritative_action,
                    condition=nested,
                    language=language,
                )
                for nested in condition.any_of
            )
        if condition.clue_discovered is not None:
            return self._clue_is_available_for_progression(
                session,
                clue_id=condition.clue_discovered.clue_id,
                clue_title=condition.clue_discovered.clue_title,
            )
        if condition.clue_state is not None:
            return self._clue_matches_condition(
                session,
                clue_id=condition.clue_state.clue_id,
                clue_title=condition.clue_state.clue_title,
                expected_state=condition.clue_state.state,
            )
        if condition.scene_is is not None:
            if (
                condition.scene_is.scene_id is not None
                and session.current_scene.scene_id != condition.scene_is.scene_id
            ):
                return False
            if (
                condition.scene_is.title is not None
                and session.current_scene.title != condition.scene_is.title
            ):
                return False
            if (
                condition.scene_is.phase is not None
                and session.current_scene.phase != condition.scene_is.phase
            ):
                return False
            return True
        if condition.current_scene_in is not None:
            if (
                condition.current_scene_in.scene_ids
                and session.current_scene.scene_id not in condition.current_scene_in.scene_ids
            ):
                return False
            if (
                condition.current_scene_in.titles
                and session.current_scene.title not in condition.current_scene_in.titles
            ):
                return False
            if (
                condition.current_scene_in.phases
                and session.current_scene.phase not in condition.current_scene_in.phases
            ):
                return False
            return True
        if condition.actor_has_status is not None:
            return self._actor_has_status(
                session,
                actor_id=condition.actor_has_status.actor_id,
                status=condition.actor_has_status.status,
            )
        if condition.any_actor_has_status is not None:
            actor_ids = (
                condition.any_actor_has_status.actor_ids
                or [participant.actor_id for participant in session.participants]
            )
            return any(
                self._actor_has_status(
                    session,
                    actor_id=actor_id,
                    status=condition.any_actor_has_status.status,
                )
                for actor_id in actor_ids
            )
        if condition.clue_visible_to_actor is not None:
            clue = self._find_clue(
                session,
                clue_id=condition.clue_visible_to_actor.clue_id,
                clue_title=condition.clue_visible_to_actor.clue_title,
            )
            return clue is not None and self._clue_visible_to_actor(
                clue,
                actor_id=condition.clue_visible_to_actor.actor_id,
            )
        if condition.actor_owns_clue is not None:
            clue = self._find_clue(
                session,
                clue_id=condition.actor_owns_clue.clue_id,
                clue_title=condition.actor_owns_clue.clue_title,
            )
            return clue is not None and condition.actor_owns_clue.actor_id in clue.owner_actor_ids
        if condition.beat_status_is is not None:
            return (
                self._resolve_beat_status(session, condition.beat_status_is.beat_id)
                == condition.beat_status_is.status
            )
        if condition.deterministic_handoff_topic_matches is not None:
            return self._deterministic_handoff_topic_matches(
                condition.deterministic_handoff_topic_matches.topic,
                authoritative_action.rules_grounding,
            )
        if condition.handoff_topic_matches is not None:
            return self._rules_topic_matches(
                condition.handoff_topic_matches.topic,
                authoritative_action.rules_grounding,
            )
        if condition.review_required is not None:
            review_required = (
                authoritative_action.source_type == AuthoritativeActionSource.REVIEWED_DRAFT
            )
            return review_required == condition.review_required.expected
        return False

    def _describe_beat_condition(self, condition: BeatCondition) -> list[str]:
        if condition.all_of:
            refs: list[str] = []
            for nested in condition.all_of:
                refs.extend(self._describe_beat_condition(nested))
            return refs
        if condition.any_of:
            refs: list[str] = []
            for nested in condition.any_of:
                refs.extend(self._describe_beat_condition(nested))
            return refs
        if condition.clue_discovered is not None:
            clue_ref = condition.clue_discovered.clue_id or condition.clue_discovered.clue_title or "unknown"
            return [f"clue_discovered:{clue_ref}"]
        if condition.clue_state is not None:
            clue_ref = condition.clue_state.clue_id or condition.clue_state.clue_title or "unknown"
            return [f"clue_state:{clue_ref}={condition.clue_state.state.value}"]
        if condition.scene_is is not None:
            return [
                "scene_is:"
                f"{condition.scene_is.scene_id or '*'}:"
                f"{condition.scene_is.title or '*'}:"
                f"{condition.scene_is.phase or '*'}"
            ]
        if condition.current_scene_in is not None:
            refs: list[str] = []
            if condition.current_scene_in.scene_ids:
                refs.append(
                    f"current_scene_in_scene_ids:{','.join(condition.current_scene_in.scene_ids)}"
                )
            if condition.current_scene_in.titles:
                refs.append(
                    f"current_scene_in_titles:{','.join(condition.current_scene_in.titles)}"
                )
            if condition.current_scene_in.phases:
                refs.append(
                    f"current_scene_in_phases:{','.join(condition.current_scene_in.phases)}"
                )
            return refs
        if condition.actor_has_status is not None:
            return [f"actor_has_status:{condition.actor_has_status.actor_id}:{condition.actor_has_status.status}"]
        if condition.any_actor_has_status is not None:
            actor_scope = (
                ",".join(condition.any_actor_has_status.actor_ids)
                if condition.any_actor_has_status.actor_ids
                else "*"
            )
            return [f"any_actor_has_status:{actor_scope}:{condition.any_actor_has_status.status}"]
        if condition.clue_visible_to_actor is not None:
            clue_ref = (
                condition.clue_visible_to_actor.clue_id
                or condition.clue_visible_to_actor.clue_title
                or "unknown"
            )
            return [f"clue_visible_to_actor:{condition.clue_visible_to_actor.actor_id}:{clue_ref}"]
        if condition.actor_owns_clue is not None:
            clue_ref = (
                condition.actor_owns_clue.clue_id
                or condition.actor_owns_clue.clue_title
                or "unknown"
            )
            return [f"actor_owns_clue:{condition.actor_owns_clue.actor_id}:{clue_ref}"]
        if condition.beat_status_is is not None:
            return [f"beat_status_is:{condition.beat_status_is.beat_id}:{condition.beat_status_is.status.value}"]
        if condition.deterministic_handoff_topic_matches is not None:
            return [f"deterministic_handoff_topic_matches:{condition.deterministic_handoff_topic_matches.topic}"]
        if condition.handoff_topic_matches is not None:
            return [f"handoff_topic_matches:{condition.handoff_topic_matches.topic}"]
        if condition.review_required is not None:
            return [f"review_required:{str(condition.review_required.expected).lower()}"]
        return []

    @staticmethod
    def _describe_declared_consequences(beat: ScenarioBeat) -> list[str]:
        refs: list[str] = []
        if beat.next_beats:
            refs.extend([f"unlock_beat:{beat_id}" for beat_id in beat.next_beats])
        for consequence in beat.consequences:
            refs.extend([f"unlock_beat:{beat_id}" for beat_id in consequence.unlock_beat_ids])
            refs.extend([f"block_beat:{beat_id}" for beat_id in consequence.block_beat_ids])
            refs.extend(
                [f"activate_fail_forward:{clue_ref}" for clue_ref in consequence.activate_fail_forward_for_clue_ids]
            )
            refs.extend(
                [
                    f"reveal_clue:{reveal_clue.clue_id or reveal_clue.clue_title or 'unknown'}"
                    for reveal_clue in consequence.reveal_clues
                ]
            )
            refs.extend(
                [
                    f"reveal_scene:{scene.scene_id or scene.scene_ref or 'unknown'}"
                    for scene in consequence.reveal_scenes
                ]
            )
            refs.extend([f"apply_status:{status.actor_id}" for status in consequence.apply_statuses])
            refs.extend([f"npc_attitude:{update.npc_id}" for update in consequence.npc_attitude_updates])
            refs.extend([f"grant_private_note:{note.actor_id}" for note in consequence.grant_private_notes])
            refs.extend(
                [f"queue_kp_prompt:{prompt.category or 'general'}" for prompt in consequence.queue_kp_prompts]
            )
            refs.extend(
                [
                    f"mark_scene_objective_complete:{mark.beat_id or beat.beat_id}"
                    for mark in consequence.mark_scene_objectives_complete
                ]
            )
        return refs

    @staticmethod
    def _describe_followup_unlock(source_beat: ScenarioBeat, target_beat_id: str) -> list[str]:
        refs = [f"unlock_beat:{target_beat_id}"]
        if target_beat_id in source_beat.next_beats:
            refs.append(f"next_beat:{source_beat.beat_id}->{target_beat_id}")
        return refs

    @staticmethod
    def _find_scenario_scene(
        scenes: list[ScenarioScene],
        *,
        scene_id: str | None,
        scene_title: str | None,
    ) -> ScenarioScene | None:
        for scene in scenes:
            if scene_id and scene.scene_id == scene_id:
                return scene
            if scene_title and scene.title == scene_title:
                return scene
        return None

    def _reveal_scene_registry_entry(
        self,
        *,
        session: SessionState,
        scene: ScenarioScene,
        source_action_id: str | None,
        trigger_reason: str,
        current_time: datetime,
    ) -> None:
        if not scene.revealed:
            scene.revealed = True
        if scene.scene_id not in session.progress_state.revealed_scene_refs:
            session.progress_state.revealed_scene_refs.append(scene.scene_id)
        self._register_scene_objectives(
            session=session,
            scene=scene,
            source_action_id=source_action_id,
            trigger_reason=trigger_reason,
            current_time=current_time,
        )

    def _register_scene_objectives(
        self,
        *,
        session: SessionState,
        scene: ScenarioScene,
        source_action_id: str | None,
        trigger_reason: str,
        current_time: datetime,
    ) -> None:
        for objective in scene.scene_objectives:
            if any(
                active.objective_id == objective.objective_id
                for active in session.progress_state.active_scene_objectives
            ):
                continue
            session.progress_state.active_scene_objectives.append(
                ActiveSceneObjective(
                    objective_id=objective.objective_id,
                    text=objective.text,
                    scene_id=scene.scene_id,
                    beat_id=objective.beat_id,
                    origin=ObjectiveOrigin.SCENE,
                    source_action_id=source_action_id,
                    trigger_reason=trigger_reason,
                    resolved=False,
                )
            )
            session.progress_state.last_updated_at = current_time

    @staticmethod
    def _scenario_has_scene_objective_for_beat(
        scenario: ScenarioScaffold,
        beat_id: str,
    ) -> bool:
        return any(
            objective.beat_id == beat_id
            for scene in scenario.scenes
            for objective in scene.scene_objectives
        )

    def _register_beat_objective(
        self,
        *,
        session: SessionState,
        beat: ScenarioBeat,
        source_action_id: str | None,
        trigger_reason: str,
    ) -> None:
        if beat.scene_objective is None:
            return
        if self._scenario_has_scene_objective_for_beat(session.scenario, beat.beat_id):
            return
        objective_id = f"beat:{beat.beat_id}"
        if any(active.objective_id == objective_id for active in session.progress_state.active_scene_objectives):
            return
        if beat.scene_objective in session.progress_state.completed_objectives:
            return
        scene_id = (
            session.current_scene.scene_id
            if self._find_scenario_scene(
                session.scenario.scenes,
                scene_id=session.current_scene.scene_id,
                scene_title=session.current_scene.title,
            )
            is not None
            else None
        )
        session.progress_state.active_scene_objectives.append(
            ActiveSceneObjective(
                objective_id=objective_id,
                text=beat.scene_objective,
                scene_id=scene_id,
                beat_id=beat.beat_id,
                origin=ObjectiveOrigin.BEAT_FALLBACK,
                source_action_id=source_action_id,
                trigger_reason=trigger_reason,
                resolved=False,
            )
        )

    @staticmethod
    def _append_completed_objective_label(
        progress_state: ScenarioProgressState,
        objective_label: str,
    ) -> None:
        if objective_label not in progress_state.completed_objectives:
            progress_state.completed_objectives.append(objective_label)
        if objective_label not in progress_state.completed_scene_objectives:
            progress_state.completed_scene_objectives.append(objective_label)

    def _record_completed_objective(
        self,
        *,
        session: SessionState,
        objective_id: str,
        text: str,
        scene_id: str | None,
        beat_id: str | None,
        origin: ObjectiveOrigin,
        source_action_id: str,
        trigger_reason: str | None,
        current_time: datetime,
    ) -> None:
        self._append_completed_objective_label(session.progress_state, text)
        if any(
            record.objective_id == objective_id and record.source_action_id == source_action_id
            for record in session.progress_state.completed_objective_history
        ):
            return
        session.progress_state.completed_objective_history.append(
            CompletedObjectiveRecord(
                objective_id=objective_id,
                text=text,
                scene_id=scene_id,
                beat_id=beat_id,
                origin=origin,
                source_action_id=source_action_id,
                trigger_reason=trigger_reason,
                completed_at=current_time,
            )
        )

    def _mark_scene_objective_complete(
        self,
        *,
        session: SessionState,
        source_action_id: str,
        beat_id: str | None,
        scene_id: str | None,
        objective_id: str | None,
        objective_label: str,
        trigger_reason: str | None,
        current_time: datetime,
    ) -> list[str]:
        completed_labels: list[str] = []
        matched = False
        for objective in session.progress_state.active_scene_objectives:
            if objective.resolved:
                continue
            if objective_id is not None and objective.objective_id != objective_id:
                continue
            if objective_id is None and scene_id is not None and objective.scene_id != scene_id:
                continue
            if objective_id is None and beat_id is not None and objective.beat_id != beat_id:
                continue
            if objective_id is None and objective_label and objective.text != objective_label and objective.beat_id != beat_id:
                continue
            objective.resolved = True
            objective.resolved_by_action_id = source_action_id
            self._record_completed_objective(
                session=session,
                objective_id=objective.objective_id,
                text=objective.text,
                scene_id=objective.scene_id,
                beat_id=objective.beat_id,
                origin=objective.origin,
                source_action_id=source_action_id,
                trigger_reason=trigger_reason or objective.trigger_reason,
                current_time=current_time,
            )
            completed_labels.append(objective.text)
            matched = True
        if matched:
            return completed_labels
        fallback_objective_id = objective_id or f"completed:{beat_id or scene_id or objective_label}"
        self._record_completed_objective(
            session=session,
            objective_id=fallback_objective_id,
            text=objective_label,
            scene_id=scene_id,
            beat_id=beat_id,
            origin=ObjectiveOrigin.BEAT_FALLBACK if beat_id is not None else ObjectiveOrigin.SCENE,
            source_action_id=source_action_id,
            trigger_reason=trigger_reason,
            current_time=current_time,
        )
        return [objective_label]

    @staticmethod
    def _clue_matches_condition(
        session: SessionState,
        *,
        clue_id: str | None,
        clue_title: str | None,
        expected_state: ClueProgressState,
    ) -> bool:
        for clue in session.scenario.clues:
            if clue_id and clue.clue_id == clue_id:
                return clue.status == expected_state
            if clue_title and clue.title == clue_title:
                return clue.status == expected_state
        return False

    @staticmethod
    def _find_clue(
        session: SessionState,
        *,
        clue_id: str | None,
        clue_title: str | None,
    ) -> ScenarioClue | None:
        for clue in session.scenario.clues:
            if clue_id and clue.clue_id == clue_id:
                return clue
            if clue_title and clue.title == clue_title:
                return clue
        return None

    def _clue_is_available_for_progression(
        self,
        session: SessionState,
        *,
        clue_id: str | None,
        clue_title: str | None,
    ) -> bool:
        clue = self._find_clue(session, clue_id=clue_id, clue_title=clue_title)
        if clue is None:
            return False
        if clue.status != ClueProgressState.UNDISCOVERED:
            return True
        return any(
            clue_ref in session.progress_state.activated_fail_forward_clues
            for clue_ref in (clue.clue_id, clue.title)
            if clue_ref
        )

    def _beat_required_clues_satisfied(self, session: SessionState, beat: ScenarioBeat) -> bool:
        if not beat.required_clues:
            return True
        discovered_refs = {
            clue_ref
            for clue in session.scenario.clues
            if clue.status != ClueProgressState.UNDISCOVERED
            for clue_ref in (clue.clue_id, clue.title)
            if clue_ref
        }
        discovered_refs.update(session.progress_state.activated_fail_forward_clues)
        return all(clue_ref in discovered_refs for clue_ref in beat.required_clues)

    def _refresh_current_beat(
        self,
        session: SessionState,
        *,
        trigger_action_id: str,
        language: LanguagePreference,
    ) -> tuple[ScenarioBeatTransitionRecord | None, list[str]]:
        progress_state = session.progress_state
        previous_current_beat = progress_state.current_beat
        if previous_current_beat in progress_state.blocked_beats or previous_current_beat in progress_state.completed_beats:
            progress_state.current_beat = None
        if progress_state.current_beat is None:
            for beat in session.scenario.beats:
                if beat.beat_id in progress_state.unlocked_beats and beat.beat_id not in progress_state.blocked_beats:
                    progress_state.current_beat = beat.beat_id
                    break
        passed_beat_ids: list[str] = []
        if previous_current_beat is not None and progress_state.current_beat != previous_current_beat:
            passed_beat_ids.append(previous_current_beat)
        if progress_state.current_beat == previous_current_beat or progress_state.current_beat is None:
            return None, passed_beat_ids
        current_beat = self._find_beat(session.scenario.beats, progress_state.current_beat)
        self._register_beat_objective(
            session=session,
            beat=current_beat,
            source_action_id=trigger_action_id,
            trigger_reason=self._message("beat_reason_current_selected", language),
        )
        return (
            self._build_beat_transition_record(
                beat=current_beat,
                transition=ScenarioBeatTransitionType.CURRENT,
                summary=self._message("beat_current", language, title=current_beat.title),
                trigger_action_id=trigger_action_id,
                reason=self._message("beat_reason_current_selected", language),
            ),
            passed_beat_ids,
        )

    @staticmethod
    def _find_beat(beats: list[ScenarioBeat], beat_id: str) -> ScenarioBeat:
        for beat in beats:
            if beat.beat_id == beat_id:
                return beat
        raise LookupError(f"beat {beat_id} was not found")

    def _activate_fail_forward_for_clue(
        self,
        *,
        session: SessionState,
        clue: ScenarioClue,
        trigger_action_id: str,
        language: LanguagePreference,
        fallback_beat: ScenarioBeat | None = None,
    ) -> list[ScenarioBeatTransitionRecord]:
        activated_refs = [
            clue_ref
            for clue_ref in (clue.clue_id, clue.title)
            if clue_ref and clue_ref not in session.progress_state.activated_fail_forward_clues
        ]
        if not activated_refs:
            return []
        session.progress_state.activated_fail_forward_clues.extend(activated_refs)
        linked_beats = [
            beat
            for beat in session.scenario.beats
            if any(clue_ref in (*beat.required_clues, *beat.optional_clues) for clue_ref in (clue.clue_id, clue.title) if clue_ref)
        ]
        if not linked_beats and fallback_beat is not None:
            linked_beats = [fallback_beat]
        return [
            self._build_beat_transition_record(
                beat=beat,
                transition=ScenarioBeatTransitionType.FAIL_FORWARD_ACTIVATED,
                summary=self._message(
                    "beat_fail_forward_activated",
                    language,
                    title=beat.title,
                    clue_title=clue.title,
                ),
                trigger_action_id=trigger_action_id,
                reason=self._message("beat_reason_fail_forward_non_blocking", language),
                consequence_refs=[f"activate_fail_forward:{clue.clue_id}"],
            )
            for beat in linked_beats
        ]

    @staticmethod
    def _deterministic_handoff_topic_matches(
        required_topic: str,
        rules_grounding: RuleGroundingSummary | None,
    ) -> bool:
        if rules_grounding is None:
            return False
        return rules_grounding.deterministic_handoff_topic == required_topic

    @staticmethod
    def _sync_beat_statuses(session: SessionState) -> None:
        progress_state = session.progress_state
        for beat in session.scenario.beats:
            if beat.beat_id in progress_state.completed_beats:
                beat.status = ScenarioBeatStatus.COMPLETED
            elif beat.beat_id in progress_state.blocked_beats:
                beat.status = ScenarioBeatStatus.BLOCKED
            elif beat.beat_id == progress_state.current_beat:
                beat.status = ScenarioBeatStatus.CURRENT
            elif beat.beat_id in progress_state.unlocked_beats:
                beat.status = ScenarioBeatStatus.UNLOCKED
            else:
                beat.status = ScenarioBeatStatus.LOCKED

    @staticmethod
    def _build_beat_transition_record(
        *,
        beat: ScenarioBeat,
        transition: ScenarioBeatTransitionType,
        summary: str,
        trigger_action_id: str,
        reason: str | None = None,
        condition_refs: list[str] | None = None,
        consequence_refs: list[str] | None = None,
    ) -> ScenarioBeatTransitionRecord:
        return ScenarioBeatTransitionRecord(
            beat_id=beat.beat_id,
            transition=transition,
            summary=summary,
            trigger_action_id=trigger_action_id,
            reason=reason,
            condition_refs=condition_refs or [],
            consequence_refs=consequence_refs or [],
        )

    @staticmethod
    def _actor_has_status(
        session: SessionState,
        *,
        actor_id: str,
        status: str,
    ) -> bool:
        character_state = session.character_states.get(actor_id)
        if character_state is None:
            return False
        return status in character_state.status_effects or status in character_state.temporary_conditions

    @staticmethod
    def _clue_visible_to_actor(
        clue: ScenarioClue,
        *,
        actor_id: str,
    ) -> bool:
        if clue.visibility_scope == VisibilityScope.SYSTEM_INTERNAL:
            return False
        if clue.visibility_scope == VisibilityScope.PUBLIC:
            return True
        return actor_id in clue.visible_to

    def _resolve_beat_status(
        self,
        session: SessionState,
        beat_id: str,
    ) -> ScenarioBeatStatus:
        if beat_id in session.progress_state.completed_beats:
            return ScenarioBeatStatus.COMPLETED
        if beat_id in session.progress_state.blocked_beats:
            return ScenarioBeatStatus.BLOCKED
        if beat_id == session.progress_state.current_beat:
            return ScenarioBeatStatus.CURRENT
        if beat_id in session.progress_state.unlocked_beats:
            return ScenarioBeatStatus.UNLOCKED
        self._find_beat(session.scenario.beats, beat_id)
        return ScenarioBeatStatus.LOCKED

    @staticmethod
    def _apply_unique_list_delta(
        items: list[str],
        *,
        add_items: list[str],
        remove_items: list[str],
    ) -> None:
        for item in add_items:
            if item not in items:
                items.append(item)
        for item in remove_items:
            while item in items:
                items.remove(item)

    def _sync_character_clue_ownership(
        self,
        session: SessionState,
        clue,
        *,
        current_time: datetime,
    ) -> None:
        for actor_id, character_state in session.character_states.items():
            owns_clue = actor_id in clue.owner_actor_ids
            has_clue = clue.clue_id in character_state.clue_ids
            if owns_clue and not has_clue:
                character_state.clue_ids.append(clue.clue_id)
                character_state.last_updated_at = current_time
            if not owns_clue and has_clue:
                character_state.clue_ids.remove(clue.clue_id)
                character_state.last_updated_at = current_time

    def _normalize_actor_ids_for_session(
        self,
        session: SessionState,
        value: Any,
        *,
        language: LanguagePreference,
    ) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_actor_ids = [value]
        elif isinstance(value, list):
            raw_actor_ids = value
        else:
            raise ValueError(self._message("invalid_actor_list", language))

        valid_actor_ids = {participant.actor_id for participant in session.participants}
        normalized_actor_ids: list[str] = []
        for item in raw_actor_ids:
            if not isinstance(item, str) or not item.strip():
                continue
            actor_id = item.strip()
            if actor_id not in valid_actor_ids:
                raise ValueError(self._message("actor_not_participant", language, actor_id=actor_id))
            if actor_id not in normalized_actor_ids:
                normalized_actor_ids.append(actor_id)
        return normalized_actor_ids

    @staticmethod
    def _resolve_bounded_value(
        *,
        current_value: int,
        absolute_value: Any,
        delta_value: Any,
        lower_bound: int,
        upper_bound: int,
    ) -> int:
        resolved_value = current_value
        if isinstance(absolute_value, int):
            resolved_value = absolute_value
        if isinstance(delta_value, int):
            resolved_value += delta_value
        return max(lower_bound, min(upper_bound, resolved_value))

    @staticmethod
    def _clue_status_label(status: ClueProgressState) -> str:
        return {
            ClueProgressState.UNDISCOVERED: "未发现",
            ClueProgressState.DISCOVERED: "已发现",
            ClueProgressState.PARTIALLY_UNDERSTOOD: "部分理解",
            ClueProgressState.SHARED_WITH_PARTY: "已共享给全队",
            ClueProgressState.PRIVATE_TO_ACTOR: "仅对特定角色可见",
        }[status]

    @staticmethod
    def _kp_prompt_status_label(
        status: KeeperPromptStatus,
        language: LanguagePreference,
    ) -> str:
        if language == LanguagePreference.ZH_CN:
            return {
                KeeperPromptStatus.PENDING: "待处理",
                KeeperPromptStatus.ACKNOWLEDGED: "已确认",
                KeeperPromptStatus.DISMISSED: "已忽略",
                KeeperPromptStatus.COMPLETED: "已完成",
            }[status]
        return {
            KeeperPromptStatus.PENDING: "pending",
            KeeperPromptStatus.ACKNOWLEDGED: "acknowledged",
            KeeperPromptStatus.DISMISSED: "dismissed",
            KeeperPromptStatus.COMPLETED: "completed",
        }[status]

    @staticmethod
    def _session_status_label(
        status: SessionStatus,
        language: LanguagePreference,
    ) -> str:
        if language == LanguagePreference.ZH_CN:
            return {
                SessionStatus.PLANNED: "计划中",
                SessionStatus.ACTIVE: "进行中",
                SessionStatus.PAUSED: "已暂停",
                SessionStatus.COMPLETED: "已完成",
            }[status]
        return {
            SessionStatus.PLANNED: "planned",
            SessionStatus.ACTIVE: "active",
            SessionStatus.PAUSED: "paused",
            SessionStatus.COMPLETED: "completed",
        }[status]

    def _apply_scene_transition_effect(
        self,
        *,
        session: SessionState,
        effect: SceneTransitionEffect,
        source_action_id: str | None,
        current_time: datetime,
        language: LanguagePreference,
    ) -> AppliedEffectRecord:
        if effect.required_current_phase and session.current_scene.phase != effect.required_current_phase:
            raise ValueError(
                self._message(
                    "scene_transition_precondition_failed",
                    language,
                    current_phase=session.current_scene.phase,
                    required_phase=effect.required_current_phase,
                )
            )
        for clue_id in effect.required_discovered_clue_ids:
            if not self._has_discovered_clue(session, clue_id):
                raise ValueError(
                    self._message("scene_transition_missing_clue", language, clue_id=clue_id)
                )
        old_scene_id = session.current_scene.scene_id
        scene_registry_entry = self._find_scenario_scene(
            session.scenario.scenes,
            scene_id=effect.scene_id,
            scene_title=effect.title,
        )
        update_values = {
            key: value.strip()
            for key, value in {
                "scene_id": scene_registry_entry.scene_id if scene_registry_entry is not None else None,
                "title": (
                    effect.title
                    if effect.title is not None
                    else scene_registry_entry.title if scene_registry_entry is not None else None
                ),
                "summary": (
                    effect.summary
                    if effect.summary is not None
                    else scene_registry_entry.summary if scene_registry_entry is not None else None
                ),
                "phase": (
                    effect.phase
                    if effect.phase is not None
                    else scene_registry_entry.phase if scene_registry_entry is not None else None
                ),
            }.items()
            if (isinstance(value, str) and value.strip())
            or (key == "scene_id" and isinstance(value, str) and value.strip())
        }
        if not update_values:
            raise ValueError(self._message("invalid_scene_transition", language))
        session.current_scene = SceneState.model_validate(
            {
                **session.current_scene.model_dump(mode="python"),
                **update_values,
            }
        )
        if scene_registry_entry is not None:
            self._reveal_scene_registry_entry(
                session=session,
                scene=scene_registry_entry,
                source_action_id=source_action_id,
                trigger_reason=self._message(
                    "scene_revealed_by_transition",
                    language,
                    title=scene_registry_entry.title,
                ),
                current_time=current_time,
            )
        self._auto_dismiss_scene_bound_pending_prompts(
            session,
            old_scene_id=old_scene_id,
            new_scene_id=session.current_scene.scene_id,
            source_action_id=source_action_id,
            current_time=current_time,
            language=language,
        )
        summary = self._message(
            "execution_scene_transition",
            language,
            title=session.current_scene.title,
        )
        if effect.consequence_tags:
            summary = (
                f"{summary}（{self._message('scene_transition_consequences', language, tags='、'.join(effect.consequence_tags))}）"
            )
        return AppliedEffectRecord(
            effect_type="scene_transition",
            target_ref=session.current_scene.scene_id,
            summary=summary,
        )

    def _has_discovered_clue(self, session: SessionState, clue_id: str) -> bool:
        for clue in session.scenario.clues:
            if clue.clue_id == clue_id:
                return clue.status != ClueProgressState.UNDISCOVERED
        return False

    def _apply_clue_state_effect(
        self,
        *,
        session: SessionState,
        effect: ClueStateEffect,
        language: LanguagePreference,
        current_time: datetime,
    ) -> AppliedEffectRecord:
        clue = self._find_clue_by_ref(
            session,
            clue_id=effect.clue_id,
            clue_title=effect.clue_title,
            language=language,
        )
        if effect.status is not None:
            clue.status = effect.status
        if effect.visibility_scope is not None:
            clue.visibility_scope = effect.visibility_scope
        if effect.visible_to:
            clue.visible_to = self._normalize_actor_ids_for_session(
                session,
                effect.visible_to,
                language=language,
            )
        self._apply_unique_list_delta(
            clue.visible_to,
            add_items=self._normalize_actor_ids_for_session(
                session,
                effect.add_visible_to,
                language=language,
            ),
            remove_items=self._normalize_actor_ids_for_session(
                session,
                effect.remove_visible_to,
                language=language,
            ),
        )
        if effect.discovered_by:
            clue.discovered_by = self._normalize_actor_ids_for_session(
                session,
                effect.discovered_by,
                language=language,
            )
        self._apply_unique_list_delta(
            clue.discovered_by,
            add_items=self._normalize_actor_ids_for_session(
                session,
                effect.add_discovered_by,
                language=language,
            ),
            remove_items=self._normalize_actor_ids_for_session(
                session,
                effect.remove_discovered_by,
                language=language,
            ),
        )
        if effect.owner_actor_ids:
            clue.owner_actor_ids = self._normalize_actor_ids_for_session(
                session,
                effect.owner_actor_ids,
                language=language,
            )
        self._apply_unique_list_delta(
            clue.owner_actor_ids,
            add_items=self._normalize_actor_ids_for_session(
                session,
                effect.add_owner_actor_ids,
                language=language,
            ),
            remove_items=self._normalize_actor_ids_for_session(
                session,
                effect.remove_owner_actor_ids,
                language=language,
            ),
        )
        if effect.discovered_via:
            clue.discovered_via = effect.discovered_via
        elif effect.activate_fail_forward:
            clue.discovered_via = clue.discovered_via or "fail_forward"

        if effect.activate_fail_forward and effect.status is None:
            clue.status = ClueProgressState.PARTIALLY_UNDERSTOOD
        if effect.share_with_party:
            clue.status = ClueProgressState.SHARED_WITH_PARTY
            clue.visibility_scope = VisibilityScope.PUBLIC
            clue.visible_to = []
        else:
            private_actor_ids = self._normalize_actor_ids_for_session(
                session,
                effect.private_to_actor_ids,
                language=language,
            )
            if private_actor_ids:
                clue.status = ClueProgressState.PRIVATE_TO_ACTOR
                clue.visibility_scope = (
                    VisibilityScope.INVESTIGATOR_PRIVATE
                    if len(private_actor_ids) == 1
                    else VisibilityScope.SHARED_SUBSET
                )
                clue.visible_to = private_actor_ids

        if clue.discovered_by and clue.status == ClueProgressState.UNDISCOVERED:
            clue.status = ClueProgressState.DISCOVERED
        if clue.status == ClueProgressState.SHARED_WITH_PARTY:
            clue.visibility_scope = VisibilityScope.PUBLIC
            clue.visible_to = []
        if clue.status == ClueProgressState.PRIVATE_TO_ACTOR and not clue.visible_to:
            clue.visible_to = list(clue.owner_actor_ids or clue.discovered_by)
            if clue.visible_to:
                clue.visibility_scope = (
                    VisibilityScope.INVESTIGATOR_PRIVATE
                    if len(clue.visible_to) == 1
                    else VisibilityScope.SHARED_SUBSET
                )

        clue.last_updated_at = current_time
        self._sync_character_clue_ownership(session, clue, current_time=current_time)
        return AppliedEffectRecord(
            effect_type="clue_state",
            target_ref=clue.clue_id,
            summary=self._message(
                "execution_clue_updated",
                language,
                title=clue.title,
                status=self._clue_status_label(clue.status),
            ),
        )

    def _apply_visibility_effect(
        self,
        *,
        session: SessionState,
        effect: VisibilityEffect,
        language: LanguagePreference,
        current_time: datetime,
    ) -> AppliedEffectRecord:
        if effect.target_kind != VisibilityEffectTarget.CLUE:
            raise ValueError(self._message("invalid_visibility_effect", language))
        clue = self._find_clue_by_ref(
            session,
            clue_id=effect.target_id,
            clue_title=effect.target_title,
            language=language,
        )
        if effect.visibility_scope is not None:
            clue.visibility_scope = effect.visibility_scope
        if effect.visible_to:
            clue.visible_to = self._normalize_actor_ids_for_session(
                session,
                effect.visible_to,
                language=language,
            )
        self._apply_unique_list_delta(
            clue.visible_to,
            add_items=self._normalize_actor_ids_for_session(
                session,
                effect.add_visible_to,
                language=language,
            ),
            remove_items=self._normalize_actor_ids_for_session(
                session,
                effect.remove_visible_to,
                language=language,
            ),
        )
        clue.last_updated_at = current_time
        return AppliedEffectRecord(
            effect_type="visibility",
            target_ref=clue.clue_id,
            summary=self._message("execution_visibility_updated", language, title=clue.title),
        )

    def _apply_character_stat_effect(
        self,
        *,
        session: SessionState,
        effect: CharacterStatEffect,
        language: LanguagePreference,
        current_time: datetime,
    ) -> AppliedEffectRecord:
        participant = self._get_participant(session, effect.actor_id, language=language)
        character_state = self._ensure_character_state(
            session,
            actor_id=effect.actor_id,
            current_time=current_time,
            language=language,
        )
        character_state.current_hit_points = self._resolve_bounded_value(
            current_value=character_state.current_hit_points,
            absolute_value=effect.current_hit_points,
            delta_value=effect.hp_delta,
            lower_bound=0,
            upper_bound=participant.character.max_hit_points,
        )
        character_state.current_magic_points = self._resolve_bounded_value(
            current_value=character_state.current_magic_points,
            absolute_value=effect.current_magic_points,
            delta_value=effect.mp_delta,
            lower_bound=0,
            upper_bound=participant.character.max_magic_points,
        )
        character_state.current_sanity = self._resolve_bounded_value(
            current_value=character_state.current_sanity,
            absolute_value=effect.current_sanity,
            delta_value=effect.san_delta,
            lower_bound=0,
            upper_bound=99,
        )
        character_state.last_updated_at = current_time
        return AppliedEffectRecord(
            effect_type="character_stats",
            target_ref=effect.actor_id,
            summary=self._message(
                "execution_character_updated",
                language,
                actor_name=participant.display_name,
            ),
        )

    def _apply_inventory_effect(
        self,
        *,
        session: SessionState,
        effect: InventoryEffect,
        language: LanguagePreference,
        current_time: datetime,
    ) -> AppliedEffectRecord:
        participant = self._get_participant(session, effect.actor_id, language=language)
        character_state = self._ensure_character_state(
            session,
            actor_id=effect.actor_id,
            current_time=current_time,
            language=language,
        )
        self._apply_unique_list_delta(
            character_state.inventory,
            add_items=self._normalize_string_list(effect.add_items),
            remove_items=self._normalize_string_list(effect.remove_items),
        )
        character_state.last_updated_at = current_time
        return AppliedEffectRecord(
            effect_type="inventory",
            target_ref=effect.actor_id,
            summary=self._message(
                "execution_inventory_updated",
                language,
                actor_name=participant.display_name,
            ),
        )

    def _apply_status_effect(
        self,
        *,
        session: SessionState,
        effect: StatusEffect,
        language: LanguagePreference,
        current_time: datetime,
    ) -> AppliedEffectRecord:
        participant = self._get_participant(session, effect.actor_id, language=language)
        character_state = self._ensure_character_state(
            session,
            actor_id=effect.actor_id,
            current_time=current_time,
            language=language,
        )
        self._apply_unique_list_delta(
            character_state.status_effects,
            add_items=self._normalize_string_list(effect.add_status_effects),
            remove_items=self._normalize_string_list(effect.remove_status_effects),
        )
        self._apply_unique_list_delta(
            character_state.temporary_conditions,
            add_items=self._normalize_string_list(effect.add_temporary_conditions),
            remove_items=self._normalize_string_list(effect.remove_temporary_conditions),
        )
        self._apply_unique_list_delta(
            character_state.private_notes,
            add_items=self._normalize_string_list(effect.add_private_notes),
            remove_items=self._normalize_string_list(effect.remove_private_notes),
        )
        self._apply_unique_list_delta(
            character_state.secret_state_refs,
            add_items=self._normalize_string_list(effect.add_secret_state_refs),
            remove_items=self._normalize_string_list(effect.remove_secret_state_refs),
        )
        character_state.last_updated_at = current_time
        return AppliedEffectRecord(
            effect_type="status",
            target_ref=effect.actor_id,
            summary=self._message(
                "execution_status_updated",
                language,
                actor_name=participant.display_name,
            ),
        )

    def _ensure_character_state(
        self,
        session: SessionState,
        *,
        actor_id: str,
        current_time: datetime,
        language: LanguagePreference,
    ) -> SessionCharacterState:
        participant = self._get_participant(session, actor_id, language=language)
        character_state = session.character_states.get(actor_id)
        if character_state is None:
            character_state = SessionCharacterState(
                actor_id=actor_id,
                current_hit_points=participant.character.max_hit_points,
                current_magic_points=participant.character.max_magic_points,
                current_sanity=participant.character.starting_sanity,
                last_updated_at=current_time,
            )
            session.character_states[actor_id] = character_state
        return character_state

    def _initialize_imported_characters(
        self,
        session: SessionState,
        *,
        current_time: datetime,
        language: LanguagePreference,
    ) -> None:
        for participant in session.participants:
            if participant.imported_character_source_id is None:
                continue
            error_context = {
                "scenario_id": session.scenario.scenario_id,
                "participant_count": len(session.participants),
                "actor_id": participant.actor_id,
                "source_id": participant.imported_character_source_id,
            }
            try:
                source = self._load_character_import_source(
                    participant.imported_character_source_id,
                    language=language,
                )
                self._apply_character_sheet_extraction_to_session(
                    session,
                    actor_id=participant.actor_id,
                    source=source,
                    sync_policy=participant.character_import_sync_policy,
                    force_apply_manual_review=False,
                    current_time=current_time,
                    language=language,
                )
            except LookupError as exc:
                message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                    "character_import_source_not_found",
                    language,
                    source_id=participant.imported_character_source_id,
                )
                raise LookupError(
                    build_structured_error_detail(
                        code="session_start_character_import_source_not_found",
                        message=message,
                        scope="session_start_character_import",
                        **error_context,
                    )
                ) from exc
            except ValueError as exc:
                message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else self._message(
                    "character_import_missing_extraction",
                    language,
                    source_id=participant.imported_character_source_id,
                )
                raise ValueError(
                    build_structured_error_detail(
                        code="session_start_character_import_invalid",
                        message=message,
                        scope="session_start_character_import",
                        **error_context,
                    )
                ) from exc

    def _apply_character_sheet_extraction_to_session(
        self,
        session: SessionState,
        *,
        actor_id: str,
        source: KnowledgeSourceState,
        sync_policy: CharacterImportSyncPolicy,
        force_apply_manual_review: bool,
        current_time: datetime,
        language: LanguagePreference,
    ) -> tuple[SessionCharacterState, CharacterImportSyncReport]:
        extraction = source.character_sheet_extraction
        if extraction is None:
            raise ValueError(
                self._message(
                    "character_import_missing_extraction",
                    language,
                    source_id=source.source_id,
                )
            )
        review = self._get_character_import_review(source, extraction)
        if (
            sync_policy == CharacterImportSyncPolicy.FORCE_REPLACE
            and review.manual_review_required
            and not force_apply_manual_review
        ):
            raise ValueError(self._message("character_import_force_review_required", language))
        participant = self._get_participant(session, actor_id, language=language)
        character_state = self._ensure_character_state(
            session,
            actor_id=actor_id,
            current_time=current_time,
            language=language,
        )
        had_import_source = character_state.import_source_id is not None
        report = CharacterImportSyncReport(
            policy=sync_policy,
            manual_review_required=review.manual_review_required,
            review_pending=character_state.import_review_pending,
            ambiguous_fields=list(review.ambiguous_fields),
            warnings=list(review.warnings),
            key_field_provenance=self._build_character_import_key_provenance(extraction),
            static_import_field_scope=list(self._STATIC_IMPORT_FIELD_SCOPE),
            session_authoritative_field_scope=list(self._SESSION_AUTHORITATIVE_FIELD_SCOPE),
        )
        if (
            sync_policy == CharacterImportSyncPolicy.INITIALIZE_IF_MISSING
            and had_import_source
        ):
            report.skipped_fields = list(
                dict.fromkeys(
                    [
                        *self._STATIC_IMPORT_FIELD_SCOPE,
                        "character_state.current_hit_points",
                        "character_state.current_magic_points",
                        "character_state.current_sanity",
                        "character_state.inventory",
                        "character_state.private_notes",
                        "character_state.secret_state_refs",
                    ]
                )
            )
            character_state.last_import_sync_policy = sync_policy
            character_state.last_import_sync_report = report
            character_state.last_updated_at = current_time
            return character_state, report

        imported_attributes = CharacterAttributes.model_validate(extraction.core_stats)
        participant.display_name = extraction.investigator_name
        participant.imported_character_source_id = source.source_id
        participant.character = participant.character.model_copy(
            update={
                "name": extraction.investigator_name,
                "occupation": extraction.occupation or participant.character.occupation,
                "age": extraction.age or participant.character.age,
                "attributes": imported_attributes,
                "skills": (
                    dict(extraction.skills)
                    if extraction.skills
                    else dict(participant.character.skills)
                ),
                "notes": self._compose_imported_character_notes(extraction),
            }
        )
        report.applied_fields.extend(
            [
                "participant.display_name",
                "participant.character.name",
                "participant.character.occupation",
                "participant.character.age",
                "participant.character.attributes",
                "participant.character.skills",
                "participant.character.notes",
                "participant.imported_character_source_id",
                "character_state.core_stat_baseline",
                "character_state.skill_baseline",
                "character_state.import_source_id",
                "character_state.import_template_profile",
                "character_state.import_manual_review_required",
            ]
        )
        character_state.core_stat_baseline = dict(extraction.core_stats)
        character_state.skill_baseline = dict(extraction.skills)
        character_state.import_source_id = source.source_id
        character_state.import_template_profile = extraction.template_profile
        character_state.import_manual_review_required = review.manual_review_required
        character_state.import_review_pending = (
            review.manual_review_required and not force_apply_manual_review
        )

        if sync_policy in {
            CharacterImportSyncPolicy.INITIALIZE_IF_MISSING,
            CharacterImportSyncPolicy.FORCE_REPLACE,
        }:
            character_state.current_hit_points = self._resolve_imported_stat_value(
                extraction.derived_stats.get("hp"),
                participant.character.max_hit_points,
            )
            character_state.current_magic_points = self._resolve_imported_stat_value(
                extraction.derived_stats.get("mp"),
                participant.character.max_magic_points,
            )
            character_state.current_sanity = self._resolve_imported_stat_value(
                extraction.derived_stats.get("san"),
                participant.character.starting_sanity,
            )
            report.applied_fields.extend(
                [
                    "character_state.current_hit_points",
                    "character_state.current_magic_points",
                    "character_state.current_sanity",
                ]
            )
        else:
            report.skipped_fields.extend(
                [
                    "character_state.current_hit_points",
                    "character_state.current_magic_points",
                    "character_state.current_sanity",
                ]
            )

        imported_private_notes = self._build_import_private_notes(extraction)
        imported_secret_refs = self._build_import_secret_state_refs(
            source_id=source.source_id,
            extraction=extraction,
            existing_refs=character_state.secret_state_refs,
            refresh_existing=sync_policy == CharacterImportSyncPolicy.FORCE_REPLACE,
        )
        if sync_policy in {
            CharacterImportSyncPolicy.INITIALIZE_IF_MISSING,
            CharacterImportSyncPolicy.FORCE_REPLACE,
        }:
            character_state.inventory = list(extraction.starting_inventory)
            character_state.private_notes = imported_private_notes
            character_state.secret_state_refs = imported_secret_refs
            report.applied_fields.extend(
                [
                    "character_state.inventory",
                    "character_state.private_notes",
                    "character_state.secret_state_refs",
                ]
            )
        elif sync_policy == CharacterImportSyncPolicy.REFRESH_WITH_MERGE:
            self._apply_unique_list_delta(
                character_state.inventory,
                add_items=list(extraction.starting_inventory),
                remove_items=[],
            )
            self._apply_unique_list_delta(
                character_state.private_notes,
                add_items=imported_private_notes,
                remove_items=[],
            )
            self._apply_unique_list_delta(
                character_state.secret_state_refs,
                add_items=imported_secret_refs,
                remove_items=[],
            )
            report.applied_fields.extend(
                [
                    "character_state.inventory",
                    "character_state.private_notes",
                    "character_state.secret_state_refs",
                ]
            )
        else:
            report.skipped_fields.extend(
                [
                    "character_state.inventory",
                    "character_state.private_notes",
                    "character_state.secret_state_refs",
                ]
            )
        report.skipped_fields.extend(
            [
                "character_state.status_effects",
                "character_state.temporary_conditions",
                "character_state.clue_ids",
            ]
        )
        report.review_pending = character_state.import_review_pending
        report.applied_fields = list(dict.fromkeys(report.applied_fields))
        report.skipped_fields = list(dict.fromkeys(report.skipped_fields))
        character_state.last_import_sync_policy = sync_policy
        character_state.last_import_sync_report = report
        character_state.last_updated_at = current_time
        return character_state, report

    @staticmethod
    def _resolve_character_import_sync_policy(
        sync_policy: CharacterImportSyncPolicy | None,
        *,
        refresh_existing: bool,
    ) -> CharacterImportSyncPolicy:
        if sync_policy is not None:
            return sync_policy
        if refresh_existing:
            return CharacterImportSyncPolicy.REFRESH_WITH_MERGE
        return CharacterImportSyncPolicy.INITIALIZE_IF_MISSING

    @staticmethod
    def _compose_imported_character_notes(
        extraction: CharacterSheetExtraction,
    ) -> str | None:
        note_parts: list[str] = []
        if extraction.background_traits:
            note_parts.append(f"背景摘要：{extraction.background_traits}")
        if extraction.campaign_notes:
            note_parts.append(f"导入备注：{extraction.campaign_notes}")
        return "\n".join(note_parts) if note_parts else None

    @staticmethod
    def _build_import_private_notes(
        extraction: CharacterSheetExtraction,
    ) -> list[str]:
        notes: list[str] = []
        if extraction.background_traits:
            notes.append(f"导入背景：{extraction.background_traits}")
        if extraction.campaign_notes:
            notes.append(f"导入备注：{extraction.campaign_notes}")
        return notes

    @staticmethod
    def _build_import_secret_state_refs(
        *,
        source_id: str,
        extraction: CharacterSheetExtraction,
        existing_refs: list[str],
        refresh_existing: bool,
    ) -> list[str]:
        imported_refs = [f"knowledge_source:{source_id}"]
        if extraction.secrets:
            imported_refs.append(f"knowledge_source:{source_id}:secrets")
        if extraction.template_profile:
            imported_refs.append(f"knowledge_source:{source_id}:template:{extraction.template_profile}")
        if not refresh_existing:
            merged_refs = list(existing_refs)
            for ref in imported_refs:
                if ref not in merged_refs:
                    merged_refs.append(ref)
            return merged_refs
        preserved_refs = [
            ref for ref in existing_refs if not ref.startswith("knowledge_source:")
        ]
        return preserved_refs + imported_refs

    @staticmethod
    def _get_character_import_review(
        source: KnowledgeSourceState,
        extraction: CharacterSheetExtraction,
    ) -> CharacterImportReview:
        if source.character_sheet_review is not None:
            return source.character_sheet_review
        return CharacterImportReview(
            template_profile_used=extraction.template_profile,
            reliably_extracted_fields=[],
            ambiguous_fields=list(extraction.ambiguous_fields),
            manual_review_required=bool(extraction.template_profile or extraction.ambiguous_fields),
            warnings=[],
        )

    @staticmethod
    def _build_character_import_key_provenance(
        extraction: CharacterSheetExtraction,
    ) -> dict[str, CharacterImportFieldSource]:
        key_fields = (
            "investigator_name",
            "occupation",
            "occupation_sequence_id",
            "core_stats.strength",
            "derived_stats.hp",
            "derived_stats.mp",
            "derived_stats.san",
            "starting_inventory",
        )
        provenance: dict[str, CharacterImportFieldSource] = {}
        for field_name in key_fields:
            field_provenance = extraction.field_provenance.get(field_name)
            if field_provenance is None:
                continue
            provenance[field_name] = CharacterImportFieldSource(
                source_workbook=field_provenance.source_workbook,
                source_sheet=field_provenance.source_sheet,
                source_anchor=field_provenance.source_anchor,
            )
        return provenance

    @staticmethod
    def _resolve_imported_stat_value(value: Any, default: int) -> int:
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return max(0, int(value))
        return default

    @staticmethod
    def _character_import_requires_manual_review(
        extraction: CharacterSheetExtraction,
    ) -> bool:
        return bool(extraction.template_profile or extraction.ambiguous_fields)

    def _find_clue_by_ref(
        self,
        session: SessionState,
        *,
        clue_id: str | None,
        clue_title: str | None,
        language: LanguagePreference,
    ) -> ScenarioClue:
        for clue in session.scenario.clues:
            if isinstance(clue_id, str) and clue.clue_id == clue_id:
                return clue
            if isinstance(clue_title, str) and clue.title == clue_title:
                return clue
        clue_ref = clue_id or clue_title or "unknown"
        raise ValueError(self._message("clue_not_found", language, clue_ref=clue_ref))

    def _update_behavior_memory(
        self,
        session: SessionState,
        reviewed_action: ReviewedAction,
        current_time: datetime,
    ) -> None:
        if not reviewed_action.learn_from_final:
            return
        if reviewed_action.actor_type != ActorType.INVESTIGATOR:
            return
        participant = self._find_participant(session, reviewed_action.actor_id)
        if participant is None or participant.kind != ParticipantKind.AI:
            return
        precedent = BehaviorPrecedent(
            actor_id=reviewed_action.actor_id,
            source_review_id=reviewed_action.review_id,
            final_text=reviewed_action.final_text,
            final_structured_action=reviewed_action.final_structured_action,
            language_preference=reviewed_action.language_preference,
            created_at=current_time,
        )
        memory = session.behavior_memory.setdefault(reviewed_action.actor_id, [])
        memory.append(precedent)
        session.behavior_memory[reviewed_action.actor_id] = memory[-self.behavior_memory_limit :]

    def _get_behavior_context(
        self,
        session: SessionState,
        actor_id: str,
    ) -> list[BehaviorPrecedent]:
        return session.behavior_memory.get(actor_id, [])[-self.behavior_memory_limit :]

    @staticmethod
    def _build_initial_character_states(
        participants: list[SessionParticipant],
        *,
        current_time: datetime,
    ) -> dict[str, SessionCharacterState]:
        return {
            participant.actor_id: SessionCharacterState(
                actor_id=participant.actor_id,
                current_hit_points=participant.character.max_hit_points,
                current_magic_points=participant.character.max_magic_points,
                current_sanity=participant.character.starting_sanity,
                last_updated_at=current_time,
            )
            for participant in participants
        }

    @staticmethod
    def _build_initial_progress_state(
        scenario: ScenarioScaffold,
        *,
        current_time: datetime,
    ) -> ScenarioProgressState:
        initially_unlocked = [
            beat.beat_id for beat in scenario.beats if beat.start_unlocked
        ]
        if not initially_unlocked and scenario.beats:
            initially_unlocked = [scenario.beats[0].beat_id]
        return ScenarioProgressState(
            current_beat=initially_unlocked[0] if initially_unlocked else None,
            unlocked_beats=initially_unlocked,
            last_updated_at=current_time,
        )

    def _save_session(
        self,
        session: SessionState,
        *,
        expected_version: int,
        reason: str,
        language: LanguagePreference,
    ) -> None:
        try:
            self.repository.save(
                session,
                reason=reason,
                expected_version=expected_version,
            )
        except ConflictError as exc:
            raise ConflictError(self._message("state_conflict", language)) from exc

    def _append_audit_log(
        self,
        session: SessionState,
        *,
        action: AuditActionType,
        actor_id: str | None,
        subject_id: str | None,
        current_time: datetime,
        details: dict[str, Any],
    ) -> None:
        session.audit_log.append(
            AuditLogEntry(
                action=action,
                actor_id=actor_id,
                subject_id=subject_id,
                session_version=session.state_version,
                details=details,
                created_at=current_time,
            )
        )

    def _classify_draft_metadata(
        self,
        *,
        action_text: str,
        structured_action: dict[str, Any],
    ) -> tuple[RiskLevel, bool, list[str], bool]:
        hinted_risk = self._parse_risk_level(structured_action.get("risk_level"))
        core_clue_flag = bool(structured_action.get("core_clue_flag", False))
        hinted_requires_approval = bool(
            structured_action.get("requires_explicit_approval", False)
        )
        affects_state = self._normalize_string_list(structured_action.get("affects_state"))
        risk_level = hinted_risk or RiskLevel.LOW
        flattened_text = " ".join(
            self._flatten_text_fragments(action_text, structured_action)
        ).lower()

        for marker_text, (state_marker, required_level) in self._SERVER_RISK_MARKERS.items():
            if marker_text not in flattened_text:
                continue
            if state_marker not in affects_state:
                affects_state.append(state_marker)
            if self._RISK_PRIORITY[required_level] > self._RISK_PRIORITY[risk_level]:
                risk_level = required_level

        requires_explicit_approval = hinted_requires_approval or core_clue_flag or risk_level in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }
        return risk_level, core_clue_flag, affects_state, requires_explicit_approval

    @staticmethod
    def _parse_risk_level(value: Any) -> RiskLevel | None:
        if not isinstance(value, str):
            return None
        try:
            return RiskLevel(value)
        except ValueError:
            return None

    @classmethod
    def _flatten_text_fragments(cls, action_text: str, value: Any) -> list[str]:
        fragments = [action_text]
        fragments.extend(cls._flatten_value(value))
        return [fragment for fragment in fragments if fragment]

    @classmethod
    def _flatten_value(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            fragments: list[str] = []
            for key, nested_value in value.items():
                fragments.append(str(key))
                fragments.extend(cls._flatten_value(nested_value))
            return fragments
        if isinstance(value, list):
            fragments: list[str] = []
            for nested_value in value:
                fragments.extend(cls._flatten_value(nested_value))
            return fragments
        return [str(value)] if value is not None else []

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str) and item and item not in normalized:
                normalized.append(item)
        return normalized

    def _resolve_language(
        self,
        *candidates: LanguagePreference | None,
    ) -> LanguagePreference:
        for candidate in candidates:
            if candidate is not None:
                return candidate
        return self.default_language

    @staticmethod
    def _message(key: str, language: LanguagePreference, **values: object) -> str:
        zh_messages = {
            "opening_scene_title": "开场",
            "session_created": "会话已创建",
            "session_created_detail": "会话已创建：{title}",
            "session_start_invalid": "会话初始化校验失败",
            "player_action_recorded": "已记录玩家行动",
            "skill_check_recorded": "已完成技能检定",
            "skill_check_session_completed": "本局已结束，当前页面不再进行新的技能检定。",
            "skill_check_no_skills": "当前角色没有可用于快速检定的技能。",
            "skill_check_skill_not_found": "技能“{skill_name}”不在当前角色的可用技能列表中。",
            "attribute_check_recorded": "已完成属性检定",
            "attribute_check_session_completed": "本局已结束，当前页面不再进行新的属性检定。",
            "attribute_check_attribute_not_found": "属性“{attribute_name}”不在当前角色的基础属性列表中。",
            "san_check_recorded": "已完成理智检定，当前 SAN 已更新",
            "san_aftermath_prompt_text": "理智后续待裁定：{actor_name}：{source_label}",
            "san_aftermath_prompt_reason": "SAN {previous_sanity} -> {current_sanity}（损失 {loss_applied}）",
            "character_hook_material_saved": "角色钩子素材已保存",
            "scene_hook_material_saved": "场景钩子素材已保存",
            "character_hook_material_seeded": "已从当前角色上下文生成初始钩子",
            "scene_hook_material_seeded": "已从当前场景上下文生成初始钩子",
            "character_hook_material_imported": "已导入外部角色 hook seed",
            "scene_hook_material_imported": "已导入外部场景 hook seed",
            "san_check_session_completed": "本局已结束，当前页面不再进行新的理智检定。",
            "san_check_invalid": "理智检定参数无效",
            "san_check_loss_invalid": "理智损失表达式“{expression}”无效；当前只支持整数或 NdM，例如 0、1、1d3、1d6。",
            "san_check_no_sanity_remaining": "当前 SAN 已为 0，不能再进行新的理智检定。",
            "manual_action_recorded": "已记录手动权威行动",
            "draft_recorded": "已记录待审核行动草稿",
            "kp_draft_recorded": "已记录 KP 待审核草稿",
            "draft_rationale": "AI 调查员行动草稿，等待人工审核",
            "kp_draft_rationale": "KP 草稿，等待人工最终确认",
            "character_import_applied": "角色导入结果已同步到会话状态",
            "character_import_source_not_found": "未找到角色导入源 {source_id}",
            "character_import_missing_extraction": "知识源 {source_id} 尚未生成人物卡提取结果",
            "character_import_not_supported": "当前会话服务未启用角色导入知识仓库",
            "character_import_force_review_required": "该导入仍需人工复核；如需强制覆盖会话状态，请显式启用 force_apply_manual_review",
            "character_import_operator_not_authorized": "只有本局 KP 可以应用角色导入结果",
            "session_import_invalid_snapshot": "导入快照校验失败",
            "session_import_missing_participant_source_warning": "导入已保留调查员 {actor_id} 的角色来源 {source_id}，但当前环境未找到该知识源；后续角色再同步可能降级。",
            "session_import_missing_character_state_source_warning": "导入已保留角色状态 {actor_id} 的 import_source_id={source_id}，但当前环境未找到该知识源；导入来源追溯与刷新可能降级。",
            "session_import_missing_secret_source_warning": "导入已保留角色状态 {actor_id} 的秘密来源引用 {ref}，但当前环境未找到知识源 {source_id}；相关来源说明可能不可用。",
            "checkpoint_created": "检查点已创建",
            "checkpoint_imported": "检查点已导入",
            "checkpoint_updated": "检查点已更新",
            "checkpoint_deleted": "检查点已删除",
            "checkpoint_not_found": "未找到检查点 {checkpoint_id}",
            "checkpoint_import_invalid_payload": "检查点导入载荷校验失败",
            "checkpoint_update_invalid": "检查点更新请求至少要提供 label 或 note。",
            "draft_approved": "已批准草稿行动并写入权威历史",
            "draft_edited": "已编辑并批准草稿行动，最终版本已写入权威历史",
            "draft_rejected": "已拒绝草稿行动，未写入权威历史",
            "draft_regenerated": "已标记原草稿为重生成，并创建新草稿",
            "session_rolled_back": "会话已回滚到版本 {version}",
            "rollback_event_detail": "会话已从版本 {from_version} 回滚到版本 {to_version}",
            "session_not_found": "未找到会话 {session_id}",
            "snapshot_not_found": "未找到版本 {version} 的会话快照",
            "viewer_id_required": "调查员视图必须提供 viewer_id",
            "viewer_not_participant": "viewer_id {viewer_id} 不属于当前会话",
            "actor_not_participant": "actor_id {actor_id} 不属于当前会话",
            "draft_not_found": "未找到草稿 {draft_id}",
            "draft_not_pending": "草稿 {draft_id} 当前不是待审核状态",
            "reviewer_not_authorized": "只有本局 KP 可以审核该草稿",
            "operator_not_authorized": "只有本局 KP 可以提交手动权威行动",
            "session_lifecycle_operator_not_authorized": "只有本局 KP 可以切换会话状态",
            "keeper_prompt_operator_not_authorized": "只有本局 KP 可以更新 KP 提示状态",
            "keeper_prompt_not_found": "未找到 KP 提示 {prompt_id}",
            "keeper_prompt_updated": "KP 提示已更新",
            "keeper_prompt_status_updated": "KP 提示状态已更新为 {status}",
            "keeper_prompt_status_invalid": "KP 提示不能从 {from_status} 变更为 {to_status}",
            "keeper_prompt_terminal": "KP 提示 {prompt_id} 已结束，不能再次变更状态",
            "keeper_prompt_aftermath_fields_unsupported": "当前 KP 提示不支持理智后续裁定字段。",
            "san_aftermath_completion_requires_resolution": "理智后续裁定在标记完成前必须填写后续标签和持续回合。",
            "objective_not_found": "未找到目标 {objective_id}",
            "objective_already_completed": "目标“{objective}”已经完成",
            "objective_not_completed": "目标“{objective}”当前尚未完成",
            "beat_not_found": "未找到剧情节点 {beat_id}",
            "beat_progression_not_available": "当前 beat 没有可手动推进的合法下一节点。",
            "beat_already_current": "剧情节点“{title}”已经是当前推进节点",
            "scene_not_found": "未找到场景 {scene_id}",
            "scene_already_revealed": "场景“{title}”已公开",
            "clue_already_revealed": "线索“{title}”已公开",
            "keeper_live_control_completed": "当前会话已完成，不能继续执行实时控场操作。",
            "state_conflict": "会话状态版本冲突，请重新加载后再试",
            "draft_stale": "草稿 {draft_id} 已过期，当前版本 {current_version} 与草稿版本 {created_at_version} 差距过大",
            "draft_superseded": "草稿 {draft_id} 已被后续草稿取代，不能再生成权威结果",
            "execution_handoff_mismatch": "执行要求的确定性交接主题 {expected_topic} 与当前规则主题 {actual_topic} 不一致",
            "invalid_scene_transition": "场景切换数据无效",
            "invalid_clue_update": "线索状态更新数据无效",
            "invalid_character_update": "角色状态更新数据无效",
            "invalid_visibility_effect": "可见性效果数据无效",
            "invalid_actor_list": "角色列表数据无效",
            "clue_not_found": "未找到线索 {clue_ref}",
            "execution_scene_transition": "场景已切换到 {title}",
            "scene_transition_precondition_failed": "场景切换前提不满足，当前阶段 {current_phase}，需要阶段 {required_phase}",
            "scene_transition_missing_clue": "场景切换缺少前置线索 {clue_id}",
            "scene_transition_consequences": "后果标签：{tags}",
            "execution_clue_updated": "线索“{title}”已更新为{status}",
            "execution_character_updated": "角色“{actor_name}”的会话状态已更新",
            "execution_inventory_updated": "角色“{actor_name}”的物品状态已更新",
            "execution_status_updated": "角色“{actor_name}”的状态效果已更新",
            "execution_visibility_updated": "线索“{title}”的可见范围已更新",
            "execution_scene_revealed": "新场景线索“{scene_ref}”已加入推进状态",
            "execution_npc_attitude_updated": "NPC“{npc_id}”态度已更新为“{attitude}”",
            "execution_kp_prompt_queued": "已为剧情节点“{title}”加入 KP 提示",
            "execution_scene_objective_completed": "场景目标“{objective}”已标记完成",
            "keeper_live_control_objective_completed": "已手动标记目标完成：{objective}",
            "keeper_live_control_objective_reopened": "已取消目标完成状态：{objective}",
            "keeper_live_control_beat_advanced": "已推进到下一 beat：{title}",
            "keeper_live_control_beat_not_reachable": "当前 beat 不能直接推进到“{title}”",
            "keeper_live_control_clue_revealed": "已公开线索：{title}",
            "keeper_live_control_scene_revealed": "已公开场景：{title}",
            "session_status_updated": "会话状态已切换为{status}",
            "session_status_transition_invalid": "会话状态不能从{from_status}切换为{to_status}",
            "scene_objective_completed_by_beat": "由剧情节点“{title}”推进完成",
            "scene_objective_completed_by_keeper": "由 KP 手动推进完成",
            "scene_revealed_initial": "初始场景已公开",
            "scene_revealed_by_transition": "因场景切换公开场景“{title}”",
            "scene_revealed_by_beat": "因剧情节点“{title}”推进公开新场景",
            "scene_revealed_by_keeper": "由 KP 手动公开场景",
            "kp_prompt_triggered_by_beat": "由剧情节点“{title}”触发",
            "beat_unlocked": "剧情节点“{title}”已解锁",
            "beat_blocked": "剧情节点“{title}”已阻塞",
            "beat_completed": "剧情节点“{title}”已完成",
            "beat_current": "当前推进节点已切换为“{title}”",
            "beat_fail_forward_activated": "剧情节点“{title}”已启用失手前进路径：{clue_title}",
            "beat_reason_unlock_conditions_met": "满足解锁条件",
            "beat_reason_block_conditions_met": "满足阻塞条件",
            "beat_reason_complete_conditions_met": "满足完成条件",
            "beat_reason_followup_unlocked": "由剧情节点“{title}”的后续结果解锁",
            "beat_reason_followup_blocked": "由剧情节点“{title}”的后续结果阻塞",
            "beat_reason_current_selected": "已切换到当前可推进节点",
            "beat_reason_keeper_selected_next": "由 KP 手动推进到下一个合法节点",
            "beat_reason_fail_forward_non_blocking": "核心线索触发失手前进，避免单点卡死",
            "unsupported_review_decision": "不支持的审核决定：{decision}",
        }
        en_messages = {
            "opening_scene_title": "Opening",
            "session_created": "Session created",
            "session_created_detail": "Session created: {title}",
            "session_start_invalid": "Session bootstrap validation failed",
            "player_action_recorded": "Player action recorded",
            "skill_check_recorded": "Skill check completed",
            "skill_check_session_completed": "This session is completed and no longer accepts new skill checks.",
            "skill_check_no_skills": "This character has no skills available for quick checks.",
            "skill_check_skill_not_found": "Skill {skill_name} is not available on this character.",
            "attribute_check_recorded": "Attribute check completed",
            "attribute_check_session_completed": "This session is completed and no longer accepts new attribute checks.",
            "attribute_check_attribute_not_found": "Attribute {attribute_name} is not available on this character.",
            "san_check_recorded": "SAN check completed and current SAN was updated",
            "san_aftermath_prompt_text": "SAN aftermath pending: {actor_name}: {source_label}",
            "san_aftermath_prompt_reason": "SAN {previous_sanity} -> {current_sanity} (loss {loss_applied})",
            "character_hook_material_saved": "Character hook material saved",
            "scene_hook_material_saved": "Scene hook material saved",
            "character_hook_material_seeded": "Seeded an initial character hook from current role context",
            "scene_hook_material_seeded": "Seeded an initial scene hook from current scene context",
            "character_hook_material_imported": "Imported an external character hook seed",
            "scene_hook_material_imported": "Imported an external scene hook seed",
            "san_check_session_completed": "This session is completed and no longer accepts new SAN checks.",
            "san_check_invalid": "SAN check request is invalid",
            "san_check_loss_invalid": "SAN loss expression {expression} is invalid; only integers or NdM such as 0, 1, 1d3, or 1d6 are supported.",
            "san_check_no_sanity_remaining": "Current SAN is already 0 and cannot take a new SAN check.",
            "manual_action_recorded": "Manual authoritative action recorded",
            "draft_recorded": "Reviewable AI draft recorded",
            "kp_draft_recorded": "Reviewable KP draft recorded",
            "draft_rationale": "AI investigator draft pending human review",
            "kp_draft_rationale": "KP draft pending final confirmation",
            "character_import_applied": "Character import applied to session state",
            "character_import_source_not_found": "Character import source {source_id} was not found",
            "character_import_missing_extraction": "Knowledge source {source_id} does not have a character-sheet extraction yet",
            "character_import_not_supported": "Character import support is not configured for this session service",
            "character_import_force_review_required": "This import still requires manual review; set force_apply_manual_review explicitly before force replacing session state",
            "character_import_operator_not_authorized": "Only the session keeper may apply character import results",
            "session_import_invalid_snapshot": "Imported snapshot validation failed",
            "session_import_missing_participant_source_warning": "Import kept participant {actor_id} source {source_id}, but that knowledge source is missing in the current environment; future character resync may degrade.",
            "session_import_missing_character_state_source_warning": "Import kept character state {actor_id} import_source_id={source_id}, but that knowledge source is missing in the current environment; source tracing and refresh may degrade.",
            "session_import_missing_secret_source_warning": "Import kept character state {actor_id} secret source ref {ref}, but knowledge source {source_id} is missing in the current environment; related provenance details may be unavailable.",
            "checkpoint_created": "Checkpoint created",
            "checkpoint_imported": "Checkpoint imported",
            "checkpoint_updated": "Checkpoint updated",
            "checkpoint_deleted": "Checkpoint deleted",
            "checkpoint_not_found": "Checkpoint {checkpoint_id} was not found",
            "checkpoint_import_invalid_payload": "Checkpoint import payload validation failed",
            "checkpoint_update_invalid": "Checkpoint updates must provide at least one of label or note.",
            "draft_approved": "Draft action approved and written to canonical history",
            "draft_edited": "Draft action edited, approved, and written to canonical history",
            "draft_rejected": "Draft action rejected and not written to canonical history",
            "draft_regenerated": "Original draft marked regenerated and a replacement draft was created",
            "session_rolled_back": "Session rolled back to version {version}",
            "rollback_event_detail": "Session rolled back from version {from_version} to version {to_version}",
            "session_not_found": "Session {session_id} was not found",
            "snapshot_not_found": "Snapshot version {version} was not found",
            "viewer_id_required": "viewer_id is required for investigator views",
            "viewer_not_participant": "viewer_id {viewer_id} is not a session participant",
            "actor_not_participant": "actor_id {actor_id} is not a session participant",
            "draft_not_found": "Draft {draft_id} was not found",
            "draft_not_pending": "Draft {draft_id} is not pending review",
            "reviewer_not_authorized": "Only the session keeper may review this draft",
            "operator_not_authorized": "Only the session keeper may submit manual authoritative actions",
            "session_lifecycle_operator_not_authorized": "Only the session keeper may change the session lifecycle status",
            "keeper_prompt_operator_not_authorized": "Only the session keeper may update KP prompt status",
            "keeper_prompt_not_found": "KP prompt {prompt_id} was not found",
            "keeper_prompt_updated": "KP prompt updated",
            "keeper_prompt_status_updated": "KP prompt status updated to {status}",
            "keeper_prompt_status_invalid": "KP prompt cannot transition from {from_status} to {to_status}",
            "keeper_prompt_terminal": "KP prompt {prompt_id} is terminal and cannot change again",
            "keeper_prompt_aftermath_fields_unsupported": "This KP prompt does not support SAN aftermath adjudication fields.",
            "san_aftermath_completion_requires_resolution": "SAN aftermath resolution requires both an aftermath label and duration before completion.",
            "objective_not_found": "Objective {objective_id} was not found",
            "objective_already_completed": "Objective {objective} is already completed",
            "objective_not_completed": "Objective {objective} is not completed",
            "beat_not_found": "Scenario beat {beat_id} was not found",
            "beat_progression_not_available": "There is no legal next beat available for manual progression.",
            "beat_already_current": "Scenario beat {title} is already current",
            "scene_not_found": "Scene {scene_id} was not found",
            "scene_already_revealed": "Scene {title} is already revealed",
            "clue_already_revealed": "Clue {title} is already revealed",
            "keeper_live_control_completed": "This session is completed and no longer accepts keeper live control actions.",
            "state_conflict": "Session state version conflict, reload and try again",
            "draft_stale": "Draft {draft_id} is stale because current version {current_version} is too far from draft version {created_at_version}",
            "draft_superseded": "Draft {draft_id} was superseded and cannot create another canonical outcome",
            "execution_handoff_mismatch": "Execution required deterministic handoff topic {expected_topic}, but current rules topic is {actual_topic}",
            "invalid_scene_transition": "Scene transition payload is invalid",
            "invalid_clue_update": "Clue state update payload is invalid",
            "invalid_character_update": "Character state update payload is invalid",
            "invalid_visibility_effect": "Visibility effect payload is invalid",
            "invalid_actor_list": "Actor list payload is invalid",
            "clue_not_found": "Clue {clue_ref} was not found",
            "execution_scene_transition": "Scene transitioned to {title}",
            "scene_transition_precondition_failed": "Scene transition precondition failed: current phase {current_phase}, required {required_phase}",
            "scene_transition_missing_clue": "Scene transition is missing prerequisite clue {clue_id}",
            "scene_transition_consequences": "consequence tags: {tags}",
            "execution_clue_updated": "Clue {title} was updated to {status}",
            "execution_character_updated": "Session character state updated for {actor_name}",
            "execution_inventory_updated": "Inventory updated for {actor_name}",
            "execution_status_updated": "Status effects updated for {actor_name}",
            "execution_visibility_updated": "Visibility updated for clue {title}",
            "execution_scene_revealed": "Scene hint {scene_ref} was added to progression state",
            "execution_npc_attitude_updated": "NPC {npc_id} attitude updated to {attitude}",
            "execution_kp_prompt_queued": "Queued a KP prompt for beat {title}",
            "execution_scene_objective_completed": "Scene objective {objective} marked complete",
            "keeper_live_control_objective_completed": "Keeper marked objective complete: {objective}",
            "keeper_live_control_objective_reopened": "Keeper reopened objective: {objective}",
            "keeper_live_control_beat_advanced": "Keeper advanced to beat: {title}",
            "keeper_live_control_beat_not_reachable": "Current beat cannot advance directly to {title}",
            "keeper_live_control_clue_revealed": "Keeper revealed clue: {title}",
            "keeper_live_control_scene_revealed": "Keeper revealed scene: {title}",
            "session_status_updated": "Session status changed to {status}",
            "session_status_transition_invalid": "Session status cannot change from {from_status} to {to_status}",
            "scene_objective_completed_by_beat": "completed by beat {title}",
            "scene_objective_completed_by_keeper": "completed by keeper live control",
            "scene_revealed_initial": "initial scene revealed",
            "scene_revealed_by_transition": "scene {title} revealed by transition",
            "scene_revealed_by_beat": "scene revealed by beat {title}",
            "scene_revealed_by_keeper": "scene revealed by keeper live control",
            "kp_prompt_triggered_by_beat": "triggered by beat {title}",
            "beat_unlocked": "Scenario beat {title} unlocked",
            "beat_blocked": "Scenario beat {title} blocked",
            "beat_completed": "Scenario beat {title} completed",
            "beat_current": "Current scenario beat changed to {title}",
            "beat_fail_forward_activated": "Scenario beat {title} activated fail-forward via {clue_title}",
            "beat_reason_unlock_conditions_met": "unlock conditions met",
            "beat_reason_block_conditions_met": "block conditions met",
            "beat_reason_complete_conditions_met": "completion conditions met",
            "beat_reason_followup_unlocked": "follow-up consequence from beat {title}",
            "beat_reason_followup_blocked": "blocking consequence from beat {title}",
            "beat_reason_current_selected": "selected as the next current beat",
            "beat_reason_keeper_selected_next": "keeper manually selected the next legal beat",
            "beat_reason_fail_forward_non_blocking": "core clue fail-forward prevented a single-point failure",
            "unsupported_review_decision": "Unsupported review decision: {decision}",
        }
        catalog = zh_messages if language == LanguagePreference.ZH_CN else en_messages
        return catalog[key].format(**values)
