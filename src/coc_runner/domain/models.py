from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from coc_runner.compat import StrEnum
from coc_runner.domain.dice import (
    AttackDefenseMode,
    AttackResolution,
    D100Roll,
    HitLocation,
    OpposedCheckResolution,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LanguagePreference(StrEnum):
    ZH_CN = "zh-CN"
    EN_US = "en-US"


class VisibilityScope(StrEnum):
    PUBLIC = "public"
    INVESTIGATOR_PRIVATE = "investigator_private"
    KP_ONLY = "kp_only"
    SHARED_SUBSET = "shared_subset"
    SHARED_CLUE = "shared_clue"
    HIDDEN_CLUE = "hidden_clue"
    SYSTEM_INTERNAL = "system_internal"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    REGENERATED = "regenerated"
    INVALIDATED = "invalidated"
    # TODO: Snapshot rollback currently restores an earlier session snapshot, so
    # reviewed/authoritative actions created after the target version disappear
    # instead of being retained and marked invalidated in-place.


class ReviewDecisionType(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    REGENERATE = "regenerate"
    MANUAL_OVERRIDE = "manual_override"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClueProgressState(StrEnum):
    UNDISCOVERED = "undiscovered"
    DISCOVERED = "discovered"
    PARTIALLY_UNDERSTOOD = "partially_understood"
    SHARED_WITH_PARTY = "shared_with_party"
    PRIVATE_TO_ACTOR = "private_to_actor"


class ParticipantKind(StrEnum):
    HUMAN = "human"
    AI = "ai"


class ActorType(StrEnum):
    KEEPER = "keeper"
    INVESTIGATOR = "investigator"
    NPC = "npc"
    SYSTEM = "system"


class ViewerRole(StrEnum):
    KEEPER = "keeper"
    INVESTIGATOR = "investigator"


class SessionStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class HitLocationStatus(StrEnum):
    ROLLED = "rolled"
    KP_OVERRIDE = "kp_override"


class KeeperWoundResolution(StrEnum):
    KEEP_RESCUE_WINDOW_OPEN = "keep_rescue_window_open"
    STABILIZE_UNCONSCIOUS = "stabilize_unconscious"
    CONFIRM_DEATH = "confirm_death"


class EventType(StrEnum):
    SESSION_STARTED = "session_started"
    PLAYER_ACTION = "player_action"
    REVIEWED_ACTION = "reviewed_action"
    MANUAL_ACTION = "manual_action"
    IMPORT = "import"
    ROLLBACK = "rollback"


class AuditActionType(StrEnum):
    DRAFT_CREATED = "draft_created"
    REVIEW_DECISION = "review_decision"
    KEEPER_PROMPT_UPDATED = "keeper_prompt_updated"
    HOOK_MATERIAL_UPDATED = "hook_material_updated"
    KEEPER_LIVE_CONTROL = "keeper_live_control"
    IMPORT = "import"
    ROLLBACK = "rollback"


class AuthoritativeActionSource(StrEnum):
    REVIEWED_DRAFT = "reviewed_draft"
    HUMAN_PLAYER = "human_player"
    MANUAL_OPERATOR = "manual_operator"


class EffectContractOrigin(StrEnum):
    EXPLICIT = "explicit"
    LEGACY_STRUCTURED_ACTION = "legacy_structured_action"


class TriggerVisibilityMode(StrEnum):
    ACTOR = "actor"
    PARTY = "party"


class VisibilityEffectTarget(StrEnum):
    CLUE = "clue"


class ScenarioBeatTransitionType(StrEnum):
    UNLOCKED = "unlocked"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CURRENT = "current"
    FAIL_FORWARD_ACTIVATED = "fail_forward_activated"


class ScenarioBeatStatus(StrEnum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    CURRENT = "current"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class KeeperPromptStatus(StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    DISMISSED = "dismissed"
    COMPLETED = "completed"


class KeeperPromptPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ObjectiveOrigin(StrEnum):
    SCENE = "scene"
    BEAT_FALLBACK = "beat_fallback"


class CharacterImportSyncPolicy(StrEnum):
    INITIALIZE_IF_MISSING = "initialize_if_missing"
    REFRESH_STATIC_FIELDS_ONLY = "refresh_static_fields_only"
    REFRESH_WITH_MERGE = "refresh_with_merge"
    FORCE_REPLACE = "force_replace"


class CharacterAttributes(BaseModel):
    strength: int = Field(ge=1, le=99)
    constitution: int = Field(ge=1, le=99)
    size: int = Field(ge=1, le=99)
    dexterity: int = Field(ge=1, le=99)
    appearance: int = Field(ge=1, le=99)
    intelligence: int = Field(ge=1, le=99)
    power: int = Field(ge=1, le=99)
    education: int = Field(ge=1, le=99)


class Character(BaseModel):
    id: str = Field(default_factory=lambda: f"character-{uuid4().hex}")
    name: str = Field(min_length=1, max_length=80)
    occupation: str = Field(min_length=1, max_length=80)
    age: int = Field(ge=15, le=99)
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    attributes: CharacterAttributes
    skills: dict[str, int] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, skills: dict[str, int]) -> dict[str, int]:
        for skill_name, score in skills.items():
            if not skill_name.strip():
                raise ValueError("skill names must not be blank")
            if not 0 <= score <= 100:
                raise ValueError("skill scores must be between 0 and 100")
        return skills

    @computed_field(return_type=int)
    @property
    def max_hit_points(self) -> int:
        return max(1, (self.attributes.constitution + self.attributes.size) // 10)

    @computed_field(return_type=int)
    @property
    def max_magic_points(self) -> int:
        return max(1, self.attributes.power // 5)

    @computed_field(return_type=int)
    @property
    def starting_sanity(self) -> int:
        return self.attributes.power


class CharacterSecrets(BaseModel):
    private_notes: list[str] = Field(default_factory=list)
    personal_clues: list[str] = Field(default_factory=list)
    personal_goals: list[str] = Field(default_factory=list)
    hidden_flags: list[str] = Field(default_factory=list)
    knowledge_history: list[str] = Field(default_factory=list)


class ClueDiscoveryTrigger(BaseModel):
    action_types: list[str] = Field(default_factory=list)
    required_topic: str | None = None
    status_on_discovery: ClueProgressState = ClueProgressState.DISCOVERED
    reveal_to: TriggerVisibilityMode = TriggerVisibilityMode.ACTOR
    assign_to_actor: bool = True
    discovered_via: str | None = None


class ClueFailForwardTrigger(BaseModel):
    action_types: list[str] = Field(default_factory=list)
    required_topic: str | None = None
    fallback_status: ClueProgressState = ClueProgressState.PARTIALLY_UNDERSTOOD
    reveal_to: TriggerVisibilityMode = TriggerVisibilityMode.ACTOR
    assign_to_actor: bool = False
    discovered_via: str = "fail_forward"


class SceneTransitionEffect(BaseModel):
    scene_id: str | None = None
    title: str | None = None
    summary: str | None = None
    phase: str | None = None
    required_current_phase: str | None = None
    required_discovered_clue_ids: list[str] = Field(default_factory=list)
    consequence_tags: list[str] = Field(default_factory=list)
    consequence_notes: list[str] = Field(default_factory=list)


class ClueStateEffect(BaseModel):
    clue_id: str | None = None
    clue_title: str | None = None
    status: ClueProgressState | None = None
    visibility_scope: VisibilityScope | None = None
    visible_to: list[str] = Field(default_factory=list)
    add_visible_to: list[str] = Field(default_factory=list)
    remove_visible_to: list[str] = Field(default_factory=list)
    discovered_by: list[str] = Field(default_factory=list)
    add_discovered_by: list[str] = Field(default_factory=list)
    remove_discovered_by: list[str] = Field(default_factory=list)
    owner_actor_ids: list[str] = Field(default_factory=list)
    add_owner_actor_ids: list[str] = Field(default_factory=list)
    remove_owner_actor_ids: list[str] = Field(default_factory=list)
    discovered_via: str | None = None
    share_with_party: bool = False
    private_to_actor_ids: list[str] = Field(default_factory=list)
    activate_fail_forward: bool = False


class CharacterStatEffect(BaseModel):
    actor_id: str
    current_hit_points: int | None = Field(default=None, ge=0)
    current_magic_points: int | None = Field(default=None, ge=0)
    current_sanity: int | None = Field(default=None, ge=0, le=99)
    hp_delta: int | None = None
    mp_delta: int | None = None
    san_delta: int | None = None


class InventoryEffect(BaseModel):
    actor_id: str
    add_items: list[str] = Field(default_factory=list)
    remove_items: list[str] = Field(default_factory=list)


class VisibilityEffect(BaseModel):
    target_kind: VisibilityEffectTarget = VisibilityEffectTarget.CLUE
    target_id: str | None = None
    target_title: str | None = None
    visibility_scope: VisibilityScope | None = None
    visible_to: list[str] = Field(default_factory=list)
    add_visible_to: list[str] = Field(default_factory=list)
    remove_visible_to: list[str] = Field(default_factory=list)


class StatusEffect(BaseModel):
    actor_id: str
    add_status_effects: list[str] = Field(default_factory=list)
    remove_status_effects: list[str] = Field(default_factory=list)
    add_temporary_conditions: list[str] = Field(default_factory=list)
    remove_temporary_conditions: list[str] = Field(default_factory=list)
    add_private_notes: list[str] = Field(default_factory=list)
    remove_private_notes: list[str] = Field(default_factory=list)
    add_secret_state_refs: list[str] = Field(default_factory=list)
    remove_secret_state_refs: list[str] = Field(default_factory=list)


class ActionEffects(BaseModel):
    scene_transitions: list[SceneTransitionEffect] = Field(default_factory=list)
    clue_state_effects: list[ClueStateEffect] = Field(default_factory=list)
    character_stat_effects: list[CharacterStatEffect] = Field(default_factory=list)
    inventory_effects: list[InventoryEffect] = Field(default_factory=list)
    visibility_effects: list[VisibilityEffect] = Field(default_factory=list)
    status_effects: list[StatusEffect] = Field(default_factory=list)


class AppliedEffectRecord(BaseModel):
    effect_type: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    reversible_by_rollback: bool = True


class ScenarioClue(BaseModel):
    clue_id: str = Field(default_factory=lambda: f"clue-{uuid4().hex}")
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1)
    status: ClueProgressState = ClueProgressState.UNDISCOVERED
    visibility_scope: VisibilityScope = VisibilityScope.PUBLIC
    visible_to: list[str] = Field(default_factory=list)
    core_clue_flag: bool = False
    alternate_paths: list[str] = Field(default_factory=list)
    fail_forward_text: str | None = None
    discovered_by: list[str] = Field(default_factory=list)
    owner_actor_ids: list[str] = Field(default_factory=list)
    discovered_via: str | None = None
    discovery_triggers: list[ClueDiscoveryTrigger] = Field(default_factory=list)
    fail_forward_triggers: list[ClueFailForwardTrigger] = Field(default_factory=list)
    last_updated_at: datetime = Field(default_factory=utc_now)
    language_preference: LanguagePreference = LanguagePreference.ZH_CN

    @model_validator(mode="after")
    def validate_core_clue_support(self) -> "ScenarioClue":
        if self.core_clue_flag and not (self.alternate_paths or self.fail_forward_text):
            raise ValueError(
                "core clues require alternate_paths or fail_forward_text for fail-forward support"
            )
        return self


class ClueStateCondition(BaseModel):
    clue_id: str | None = None
    clue_title: str | None = None
    state: ClueProgressState

    @model_validator(mode="after")
    def validate_clue_ref(self) -> "ClueStateCondition":
        if self.clue_id or self.clue_title:
            return self
        raise ValueError("clue_state conditions require clue_id or clue_title")


class ClueDiscoveredCondition(BaseModel):
    clue_id: str | None = None
    clue_title: str | None = None

    @model_validator(mode="after")
    def validate_clue_ref(self) -> "ClueDiscoveredCondition":
        if self.clue_id or self.clue_title:
            return self
        raise ValueError("clue_discovered conditions require clue_id or clue_title")


class SceneIsCondition(BaseModel):
    scene_id: str | None = None
    title: str | None = None
    phase: str | None = None

    @model_validator(mode="after")
    def validate_scene_ref(self) -> "SceneIsCondition":
        if self.scene_id or self.title or self.phase:
            return self
        raise ValueError("scene_is conditions require scene_id, title, or phase")


class ActorHasStatusCondition(BaseModel):
    actor_id: str
    status: str = Field(min_length=1)


class AnyActorHasStatusCondition(BaseModel):
    status: str = Field(min_length=1)
    actor_ids: list[str] = Field(default_factory=list)


class ClueVisibleToActorCondition(BaseModel):
    actor_id: str
    clue_id: str | None = None
    clue_title: str | None = None

    @model_validator(mode="after")
    def validate_clue_ref(self) -> "ClueVisibleToActorCondition":
        if self.clue_id or self.clue_title:
            return self
        raise ValueError("clue_visible_to_actor conditions require clue_id or clue_title")


class ActorOwnsClueCondition(BaseModel):
    actor_id: str
    clue_id: str | None = None
    clue_title: str | None = None

    @model_validator(mode="after")
    def validate_clue_ref(self) -> "ActorOwnsClueCondition":
        if self.clue_id or self.clue_title:
            return self
        raise ValueError("actor_owns_clue conditions require clue_id or clue_title")


class BeatStatusIsCondition(BaseModel):
    beat_id: str = Field(min_length=1)
    status: ScenarioBeatStatus


class CurrentSceneInCondition(BaseModel):
    scene_ids: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    phases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scene_scope(self) -> "CurrentSceneInCondition":
        if self.scene_ids or self.titles or self.phases:
            return self
        raise ValueError("current_scene_in conditions require scene_ids, titles, or phases")


class HandoffTopicCondition(BaseModel):
    topic: str = Field(min_length=1)


class DeterministicHandoffTopicCondition(BaseModel):
    topic: str = Field(min_length=1)


class ReviewRequiredCondition(BaseModel):
    expected: bool = True


class BeatCondition(BaseModel):
    all_of: list["BeatCondition"] = Field(default_factory=list)
    any_of: list["BeatCondition"] = Field(default_factory=list)
    clue_discovered: ClueDiscoveredCondition | None = None
    clue_state: ClueStateCondition | None = None
    scene_is: SceneIsCondition | None = None
    current_scene_in: CurrentSceneInCondition | None = None
    actor_has_status: ActorHasStatusCondition | None = None
    any_actor_has_status: AnyActorHasStatusCondition | None = None
    clue_visible_to_actor: ClueVisibleToActorCondition | None = None
    actor_owns_clue: ActorOwnsClueCondition | None = None
    beat_status_is: BeatStatusIsCondition | None = None
    deterministic_handoff_topic_matches: DeterministicHandoffTopicCondition | None = None
    handoff_topic_matches: HandoffTopicCondition | None = None
    review_required: ReviewRequiredCondition | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "BeatCondition":
        branches = 0
        if self.all_of:
            branches += 1
        if self.any_of:
            branches += 1
        if any(
            leaf is not None
            for leaf in (
                self.clue_discovered,
                self.clue_state,
                self.scene_is,
                self.current_scene_in,
                self.actor_has_status,
                self.any_actor_has_status,
                self.clue_visible_to_actor,
                self.actor_owns_clue,
                self.beat_status_is,
                self.deterministic_handoff_topic_matches,
                self.handoff_topic_matches,
                self.review_required,
            )
        ):
            branches += 1
        if branches != 1:
            raise ValueError(
                "beat conditions must define exactly one of all_of, any_of, or a single leaf condition"
            )
        return self


class BeatConsequence(BaseModel):
    unlock_beat_ids: list[str] = Field(default_factory=list)
    block_beat_ids: list[str] = Field(default_factory=list)
    activate_fail_forward_for_clue_ids: list[str] = Field(default_factory=list)
    reveal_clues: list["RevealClueConsequence"] = Field(default_factory=list)
    reveal_scenes: list["RevealSceneConsequence"] = Field(default_factory=list)
    apply_statuses: list["ApplyStatusConsequence"] = Field(default_factory=list)
    npc_attitude_updates: list["UpdateNPCAttitudeConsequence"] = Field(default_factory=list)
    grant_private_notes: list["GrantPrivateNoteConsequence"] = Field(default_factory=list)
    queue_kp_prompts: list["QueueKPPromptConsequence"] = Field(default_factory=list)
    mark_scene_objectives_complete: list["MarkSceneObjectiveCompleteConsequence"] = Field(
        default_factory=list
    )


class RevealClueConsequence(BaseModel):
    clue_id: str | None = None
    clue_title: str | None = None
    status: ClueProgressState = ClueProgressState.DISCOVERED
    share_with_party: bool = True
    visible_to_actor_ids: list[str] = Field(default_factory=list)
    owner_actor_ids: list[str] = Field(default_factory=list)
    discovered_by_actor_ids: list[str] = Field(default_factory=list)
    discovered_via: str | None = None

    @model_validator(mode="after")
    def validate_clue_ref(self) -> "RevealClueConsequence":
        if self.clue_id or self.clue_title:
            return self
        raise ValueError("reveal_clue consequences require clue_id or clue_title")


class RevealSceneConsequence(BaseModel):
    scene_id: str | None = None
    scene_ref: str | None = None
    summary: str | None = None

    @model_validator(mode="after")
    def validate_scene_ref(self) -> "RevealSceneConsequence":
        if self.scene_id or self.scene_ref:
            return self
        raise ValueError("reveal_scene consequences require scene_id or scene_ref")


class ApplyStatusConsequence(BaseModel):
    actor_id: str
    add_status_effects: list[str] = Field(default_factory=list)
    add_temporary_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "ApplyStatusConsequence":
        if self.add_status_effects or self.add_temporary_conditions:
            return self
        raise ValueError("apply_status consequences require at least one status or temporary condition")


class UpdateNPCAttitudeConsequence(BaseModel):
    npc_id: str = Field(min_length=1)
    attitude: str = Field(min_length=1)
    note: str | None = None


class GrantPrivateNoteConsequence(BaseModel):
    actor_id: str
    note: str = Field(min_length=1)


class QueueKPPromptConsequence(BaseModel):
    prompt_text: str = Field(min_length=1)
    category: str | None = None
    scene_id: str | None = None
    priority: KeeperPromptPriority = KeeperPromptPriority.MEDIUM
    assigned_to: str | None = None
    expires_after_beat: str | None = None
    reason: str | None = None


class MarkSceneObjectiveCompleteConsequence(BaseModel):
    objective_id: str | None = None
    scene_id: str | None = None
    objective_label: str | None = None
    beat_id: str | None = None


class SceneObjective(BaseModel):
    objective_id: str = Field(default_factory=lambda: f"objective-{uuid4().hex}")
    text: str = Field(min_length=1)
    beat_id: str | None = None
    notes: str | None = None


class SuggestionHookMaterial(BaseModel):
    hook_id: str = Field(default_factory=lambda: f"hook-{uuid4().hex}")
    hook_label: str = Field(min_length=1, max_length=80)
    hook_text: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScenarioScene(BaseModel):
    scene_id: str = Field(default_factory=lambda: f"scene-{uuid4().hex}")
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1)
    phase: str = Field(default="investigation", min_length=1, max_length=40)
    visibility_scope: VisibilityScope = VisibilityScope.PUBLIC
    visible_to: list[str] = Field(default_factory=list)
    revealed: bool = False
    linked_clue_ids: list[str] = Field(default_factory=list)
    scene_objectives: list[SceneObjective] = Field(default_factory=list)
    keeper_notes: list[str] = Field(default_factory=list)
    runtime_notes: list[str] = Field(default_factory=list)
    suggestion_hooks: list[SuggestionHookMaterial] = Field(default_factory=list)


class ScenarioNPC(BaseModel):
    npc_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    role: str | None = None
    initial_attitude: str = Field(default="neutral", min_length=1)
    keeper_notes: list[str] = Field(default_factory=list)


class ScenarioBeat(BaseModel):
    beat_id: str = Field(default_factory=lambda: f"beat-{uuid4().hex}")
    title: str = Field(min_length=1, max_length=120)
    status: ScenarioBeatStatus = ScenarioBeatStatus.LOCKED
    scene_objective: str | None = None
    required_clues: list[str] = Field(default_factory=list)
    optional_clues: list[str] = Field(default_factory=list)
    start_unlocked: bool = False
    unlock_conditions: BeatCondition | None = None
    block_conditions: BeatCondition | None = None
    complete_conditions: BeatCondition | None = None
    unlock_when: BeatCondition | None = None
    block_when: BeatCondition | None = None
    complete_when: BeatCondition | None = None
    consequences: list[BeatConsequence] = Field(default_factory=list)
    next_beats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_progression_fields(self) -> "ScenarioBeat":
        if self.unlock_conditions is None and self.unlock_when is not None:
            self.unlock_conditions = self.unlock_when
        if self.unlock_when is None and self.unlock_conditions is not None:
            self.unlock_when = self.unlock_conditions
        if self.block_conditions is None and self.block_when is not None:
            self.block_conditions = self.block_when
        if self.block_when is None and self.block_conditions is not None:
            self.block_when = self.block_conditions
        if self.complete_conditions is None and self.complete_when is not None:
            self.complete_conditions = self.complete_when
        if self.complete_when is None and self.complete_conditions is not None:
            self.complete_when = self.complete_conditions
        return self


class ScenarioBeatTransitionRecord(BaseModel):
    beat_id: str
    transition: ScenarioBeatTransitionType
    summary: str = Field(min_length=1)
    trigger_action_id: str | None = None
    reason: str | None = None
    condition_refs: list[str] = Field(default_factory=list)
    consequence_refs: list[str] = Field(default_factory=list)


class ActiveSceneObjective(BaseModel):
    objective_id: str
    text: str = Field(min_length=1)
    scene_id: str | None = None
    beat_id: str | None = None
    origin: ObjectiveOrigin = ObjectiveOrigin.SCENE
    source_action_id: str | None = None
    trigger_reason: str | None = None
    resolved: bool = False
    resolved_by_action_id: str | None = None


class CompletedObjectiveRecord(BaseModel):
    objective_id: str
    text: str = Field(min_length=1)
    scene_id: str | None = None
    beat_id: str | None = None
    origin: ObjectiveOrigin = ObjectiveOrigin.SCENE
    source_action_id: str | None = None
    trigger_reason: str | None = None
    completed_at: datetime = Field(default_factory=utc_now)


class QueuedKPPrompt(BaseModel):
    prompt_id: str = Field(default_factory=lambda: f"kp-prompt-{uuid4().hex}")
    prompt_text: str = Field(min_length=1)
    beat_id: str | None = None
    scene_id: str | None = None
    source_action_id: str | None = None
    category: str | None = None
    priority: KeeperPromptPriority = KeeperPromptPriority.MEDIUM
    assigned_to: str | None = None
    expires_after_beat: str | None = None
    aftermath_label: str | None = Field(default=None, max_length=80)
    duration_rounds: int | None = Field(default=None, ge=1)
    san_actor_id: str | None = Field(default=None, max_length=80)
    san_actor_name: str | None = Field(default=None, max_length=80)
    san_source_label: str | None = Field(default=None, max_length=120)
    san_previous_sanity: int | None = Field(default=None, ge=0, le=99)
    san_current_sanity: int | None = Field(default=None, ge=0, le=99)
    san_loss_applied: int | None = Field(default=None, ge=0)
    combat_actor_id: str | None = Field(default=None, max_length=80)
    combat_actor_name: str | None = Field(default=None, max_length=80)
    notes: list[str] = Field(default_factory=list)
    status: KeeperPromptStatus = KeeperPromptStatus.PENDING
    trigger_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    acknowledged_at: datetime | None = None
    dismissed_at: datetime | None = None
    completed_at: datetime | None = None


class SanAftermathSuggestion(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    duration_rounds: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=160)


class KeeperWorkflowSummary(BaseModel):
    active_prompt_count: int = 0
    unresolved_objective_count: int = 0
    recently_completed_objectives: list[CompletedObjectiveRecord] = Field(default_factory=list)
    recent_beat_transitions: list[ScenarioBeatTransitionRecord] = Field(default_factory=list)
    prompt_lines: list[str] = Field(default_factory=list)
    objective_lines: list[str] = Field(default_factory=list)
    completed_objective_lines: list[str] = Field(default_factory=list)
    progression_lines: list[str] = Field(default_factory=list)
    summary_lines: list[str] = Field(default_factory=list)


class KeeperWorkflowState(BaseModel):
    active_prompts: list[QueuedKPPrompt] = Field(default_factory=list)
    unresolved_objectives: list[ActiveSceneObjective] = Field(default_factory=list)
    summary: KeeperWorkflowSummary = Field(default_factory=KeeperWorkflowSummary)


class KeeperContextPackIdentity(BaseModel):
    session_id: str
    scenario_title: str | None = None
    playtest_group: str | None = None
    status: SessionStatus = SessionStatus.PLANNED
    current_scene: str | None = None
    current_beat: str | None = None
    current_beat_title: str | None = None


class KeeperContextPackCombatSummary(BaseModel):
    in_combat: bool = False
    current_actor_id: str | None = None
    current_actor_name: str | None = None
    round_number: int | None = None
    turn_order_count: int = 0
    wound_follow_up_count: int = 0
    pending_damage_actor_count: int = 0
    summary_line: str | None = None


class KeeperContextPack(BaseModel):
    identity: KeeperContextPackIdentity
    summary_lines: list[str] = Field(default_factory=list)
    recent_event_lines: list[str] = Field(default_factory=list)
    objective_lines: list[str] = Field(default_factory=list)
    prompt_lines: list[str] = Field(default_factory=list)
    combat: KeeperContextPackCombatSummary = Field(default_factory=KeeperContextPackCombatSummary)
    recent_keeper_notes: list[str] = Field(default_factory=list)
    knowledge_highlights: list[str] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)
    narrative_work_note: str | None = None
    disclaimer: str = Field(
        default="这是 keeper-side 当前工作摘要 / context pack，只用于查看与 AI copilot 输入，不是 authoritative truth。"
    )


class KeeperCompressedContext(BaseModel):
    session_id: str
    scenario_title: str | None = None
    status: SessionStatus = SessionStatus.PLANNED
    current_scene: str | None = None
    current_beat_title: str | None = None
    situation_summary: str = Field(min_length=1, max_length=220)
    immediate_pressures: list[str] = Field(default_factory=list)
    next_focus: list[str] = Field(default_factory=list)
    active_prompt_briefs: list[str] = Field(default_factory=list)
    combat_summary: str | None = Field(default=None, max_length=160)
    narrative_work_summary: str | None = Field(default=None, max_length=160)
    knowledge_direction: list[str] = Field(default_factory=list)
    disclaimer: str = Field(
        default="这是 keeper-side 的 compressed context / compact recap，只用于查看与 AI 输入，不是 authoritative truth。"
    )


class ScenarioProgressState(BaseModel):
    current_beat: str | None = None
    unlocked_beats: list[str] = Field(default_factory=list)
    blocked_beats: list[str] = Field(default_factory=list)
    completed_beats: list[str] = Field(default_factory=list)
    activated_fail_forward_clues: list[str] = Field(default_factory=list)
    revealed_scene_refs: list[str] = Field(default_factory=list)
    completed_objectives: list[str] = Field(default_factory=list)
    # Legacy compatibility mirror. Prefer completed_objectives in new code.
    completed_scene_objectives: list[str] = Field(default_factory=list)
    completed_objective_history: list[CompletedObjectiveRecord] = Field(default_factory=list)
    npc_attitudes: dict[str, str] = Field(default_factory=dict)
    queued_kp_prompts: list[QueuedKPPrompt] = Field(default_factory=list)
    active_scene_objectives: list[ActiveSceneObjective] = Field(default_factory=list)
    transition_history: list[ScenarioBeatTransitionRecord] = Field(default_factory=list)
    last_updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_completed_objective_lists(self) -> "ScenarioProgressState":
        merged: list[str] = []
        for value in [*self.completed_objectives, *self.completed_scene_objectives]:
            if value not in merged:
                merged.append(value)
        self.completed_objectives = list(merged)
        self.completed_scene_objectives = list(merged)
        return self


class CharacterImportFieldSource(BaseModel):
    source_workbook: str
    source_sheet: str
    source_anchor: str | None = None


class CharacterImportSyncReport(BaseModel):
    policy: CharacterImportSyncPolicy
    manual_review_required: bool = False
    review_pending: bool = False
    applied_fields: list[str] = Field(default_factory=list)
    skipped_fields: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    key_field_provenance: dict[str, CharacterImportFieldSource] = Field(default_factory=dict)
    static_import_field_scope: list[str] = Field(default_factory=list)
    session_authoritative_field_scope: list[str] = Field(default_factory=list)


class SessionCharacterState(BaseModel):
    actor_id: str
    current_hit_points: int = Field(ge=0)
    current_magic_points: int = Field(ge=0)
    current_sanity: int = Field(ge=0, le=99)
    core_stat_baseline: dict[str, int] = Field(default_factory=dict)
    skill_baseline: dict[str, int] = Field(default_factory=dict)
    inventory: list[str] = Field(default_factory=list)
    status_effects: list[str] = Field(default_factory=list)
    temporary_conditions: list[str] = Field(default_factory=list)
    heavy_wound_active: bool = False
    is_unconscious: bool = False
    is_dying: bool = False
    is_stable: bool = False
    rescue_window_open: bool = False
    death_confirmed: bool = False
    clue_ids: list[str] = Field(default_factory=list)
    private_notes: list[str] = Field(default_factory=list)
    secret_state_refs: list[str] = Field(default_factory=list)
    import_source_id: str | None = None
    import_template_profile: str | None = None
    import_manual_review_required: bool = False
    import_review_pending: bool = False
    last_import_sync_policy: CharacterImportSyncPolicy | None = None
    last_import_sync_report: CharacterImportSyncReport | None = None
    pending_damage_context: "PendingDamageContext | None" = None
    last_updated_at: datetime = Field(default_factory=utc_now)


class PendingDamageContext(BaseModel):
    source_actor_id: str = Field(min_length=1, max_length=80)
    target_actor_id: str = Field(min_length=1, max_length=80)
    target_display_name: str = Field(min_length=1, max_length=80)
    attack_mode: Literal["melee", "ranged"]
    attack_label: str = Field(min_length=1, max_length=80)
    created_at: datetime = Field(default_factory=utc_now)


class CombatTurnEntry(BaseModel):
    actor_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=80)
    dexterity: int = Field(ge=1, le=99)


class CombatContext(BaseModel):
    participant_actor_ids: list[str] = Field(default_factory=list)
    turn_order: list[CombatTurnEntry] = Field(default_factory=list)
    current_turn_index: int = Field(default=0, ge=0)
    round_number: int = Field(default=1, ge=1)
    current_actor_id: str | None = None
    next_actor_id: str | None = None
    manual_tie_break_required: bool = False
    started_at: datetime = Field(default_factory=utc_now)


class ScenarioScaffold(BaseModel):
    scenario_id: str = Field(default_factory=lambda: f"scenario-{uuid4().hex}")
    title: str = Field(min_length=1, max_length=120)
    hook: str = Field(min_length=1)
    starting_location: str = Field(min_length=1, max_length=120)
    start_scene_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    scenes: list[ScenarioScene] = Field(default_factory=list)
    clues: list[ScenarioClue] = Field(default_factory=list)
    beats: list[ScenarioBeat] = Field(default_factory=list)
    npcs: list[ScenarioNPC] = Field(default_factory=list)
    language_preference: LanguagePreference | None = None

    @model_validator(mode="after")
    def validate_beats(self) -> "ScenarioScaffold":
        def find_duplicate(values: list[str]) -> str | None:
            seen: set[str] = set()
            for value in values:
                if value in seen:
                    return value
                seen.add(value)
            return None

        def collect_condition_beat_refs(condition: BeatCondition | None) -> set[str]:
            if condition is None:
                return set()
            refs: set[str] = set()
            for nested in condition.all_of:
                refs.update(collect_condition_beat_refs(nested))
            for nested in condition.any_of:
                refs.update(collect_condition_beat_refs(nested))
            if condition.beat_status_is is not None:
                refs.add(condition.beat_status_is.beat_id)
            return refs

        def collect_condition_status_refs(
            condition: BeatCondition | None,
            *,
            status: ScenarioBeatStatus,
        ) -> set[str]:
            if condition is None:
                return set()
            refs: set[str] = set()
            for nested in condition.all_of:
                refs.update(collect_condition_status_refs(nested, status=status))
            for nested in condition.any_of:
                refs.update(collect_condition_status_refs(nested, status=status))
            if (
                condition.beat_status_is is not None
                and condition.beat_status_is.status == status
            ):
                refs.add(condition.beat_status_is.beat_id)
            return refs

        def expand_condition_branches(condition: BeatCondition | None) -> list[list[BeatCondition]]:
            if condition is None:
                return []
            if condition.all_of:
                branches: list[list[BeatCondition]] = [[]]
                for nested in condition.all_of:
                    nested_branches = expand_condition_branches(nested)
                    expanded: list[list[BeatCondition]] = []
                    for branch in branches:
                        for nested_branch in nested_branches:
                            expanded.append([*branch, *nested_branch])
                    branches = expanded
                return branches
            if condition.any_of:
                branches: list[list[BeatCondition]] = []
                for nested in condition.any_of:
                    branches.extend(expand_condition_branches(nested))
                return branches
            return [[condition]]

        def normalize_condition_payload(condition: BeatCondition) -> dict[str, Any]:
            return condition.model_dump(mode="python", exclude_none=True)

        def analyze_unsatisfiable_scene_branch(branch: list[BeatCondition]) -> str | None:
            candidate_scenes = list(self.scenes)
            scene_constraint_refs: list[str] = []
            has_scene_constraint = False

            for leaf in branch:
                if leaf.scene_is is not None:
                    has_scene_constraint = True
                    if leaf.scene_is.scene_id is not None:
                        scene_constraint_refs.append(f"scene_is.scene_id={leaf.scene_is.scene_id}")
                    if leaf.scene_is.title is not None:
                        scene_constraint_refs.append(f"scene_is.title={leaf.scene_is.title}")
                    if leaf.scene_is.phase is not None:
                        scene_constraint_refs.append(f"scene_is.phase={leaf.scene_is.phase}")
                    candidate_scenes = [
                        scene
                        for scene in candidate_scenes
                        if (
                            (leaf.scene_is.scene_id is None or scene.scene_id == leaf.scene_is.scene_id)
                            and (leaf.scene_is.title is None or scene.title == leaf.scene_is.title)
                            and (leaf.scene_is.phase is None or scene.phase == leaf.scene_is.phase)
                        )
                    ]
                if leaf.current_scene_in is not None:
                    has_scene_constraint = True
                    if leaf.current_scene_in.scene_ids:
                        scene_constraint_refs.append(
                            "current_scene_in.scene_ids="
                            + ",".join(leaf.current_scene_in.scene_ids)
                        )
                    if leaf.current_scene_in.titles:
                        scene_constraint_refs.append(
                            "current_scene_in.titles=" + ",".join(leaf.current_scene_in.titles)
                        )
                    if leaf.current_scene_in.phases:
                        scene_constraint_refs.append(
                            "current_scene_in.phases=" + ",".join(leaf.current_scene_in.phases)
                        )
                    candidate_scenes = [
                        scene
                        for scene in candidate_scenes
                        if (
                            (
                                not leaf.current_scene_in.scene_ids
                                or scene.scene_id in leaf.current_scene_in.scene_ids
                            )
                            and (
                                not leaf.current_scene_in.titles
                                or scene.title in leaf.current_scene_in.titles
                            )
                            and (
                                not leaf.current_scene_in.phases
                                or scene.phase in leaf.current_scene_in.phases
                            )
                        )
                    ]
            if has_scene_constraint and not candidate_scenes:
                return "no scenario scene matches " + "; ".join(scene_constraint_refs)
            return None

        def analyze_unsatisfiable_scene_condition(condition: BeatCondition | None) -> str | None:
            if condition is None:
                return None
            branches = expand_condition_branches(condition)
            if not branches:
                return None
            branch_reasons: list[str] = []
            for branch in branches:
                reason = analyze_unsatisfiable_scene_branch(branch)
                if reason is None:
                    return None
                branch_reasons.append(reason)
            return branch_reasons[0] if branch_reasons else None

        def analyze_unsatisfiable_complete_branch(
            branch: list[BeatCondition],
            *,
            beat_id: str,
        ) -> str | None:
            beat_statuses: dict[str, set[ScenarioBeatStatus]] = {}

            for leaf in branch:
                if leaf.beat_status_is is not None:
                    beat_statuses.setdefault(leaf.beat_status_is.beat_id, set()).add(
                        leaf.beat_status_is.status
                    )

            for target_beat_id, statuses in beat_statuses.items():
                if len(statuses) > 1:
                    status_list = ", ".join(sorted(status.value for status in statuses))
                    return (
                        f"conflicting beat_status_is requirements for beat {target_beat_id}: "
                        f"{status_list}"
                    )

            impossible_self_statuses = beat_statuses.get(beat_id, set()) & {
                ScenarioBeatStatus.LOCKED,
                ScenarioBeatStatus.BLOCKED,
                ScenarioBeatStatus.COMPLETED,
            }
            if impossible_self_statuses:
                status_list = ", ".join(sorted(status.value for status in impossible_self_statuses))
                return f"it requires its own status to already be {status_list}"

            return analyze_unsatisfiable_scene_branch(branch)

        def analyze_unsatisfiable_complete_condition(
            condition: BeatCondition | None,
            *,
            beat_id: str,
        ) -> str | None:
            if condition is None:
                return None
            branches = expand_condition_branches(condition)
            if not branches:
                return None
            branch_reasons: list[str] = []
            for branch in branches:
                reason = analyze_unsatisfiable_complete_branch(branch, beat_id=beat_id)
                if reason is None:
                    return None
                branch_reasons.append(reason)
            return branch_reasons[0] if branch_reasons else None

        def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
            state: dict[str, int] = {}
            path: list[str] = []

            def visit(node: str) -> list[str] | None:
                state[node] = 1
                path.append(node)
                for neighbor in graph[node]:
                    neighbor_state = state.get(neighbor, 0)
                    if neighbor_state == 0:
                        cycle = visit(neighbor)
                        if cycle is not None:
                            return cycle
                    elif neighbor_state == 1:
                        start_index = path.index(neighbor)
                        return [*path[start_index:], neighbor]
                path.pop()
                state[node] = 2
                return None

            for node in graph:
                if state.get(node, 0) == 0:
                    cycle = visit(node)
                    if cycle is not None:
                        return cycle
            return None

        scene_ids = [scene.scene_id for scene in self.scenes]
        duplicate_scene_id = find_duplicate(scene_ids)
        if duplicate_scene_id is not None:
            raise ValueError(f"scenario scene_id {duplicate_scene_id} must be unique")
        scene_titles = [scene.title for scene in self.scenes]
        duplicate_scene_title = find_duplicate(scene_titles)
        if duplicate_scene_title is not None:
            raise ValueError(f"scenario scene title {duplicate_scene_title} must be unique")
        if self.start_scene_id is not None and self.start_scene_id not in set(scene_ids):
            raise ValueError(f"scenario start_scene_id {self.start_scene_id} was not found")
        clue_ids = [clue.clue_id for clue in self.clues]
        duplicate_clue_id = find_duplicate(clue_ids)
        if duplicate_clue_id is not None:
            raise ValueError(f"scenario clue_id {duplicate_clue_id} must be unique")
        beat_ids = [beat.beat_id for beat in self.beats]
        duplicate_beat_id = find_duplicate(beat_ids)
        if duplicate_beat_id is not None:
            raise ValueError(f"scenario beat_id {duplicate_beat_id} must be unique")
        scene_id_set = set(scene_ids)
        scene_title_set = {scene.title for scene in self.scenes}
        scene_phase_set = {scene.phase for scene in self.scenes}
        clue_refs = {
            clue_ref
            for clue in self.clues
            for clue_ref in (clue.clue_id, clue.title)
            if clue_ref
        }
        beat_id_set = set(beat_ids)
        objective_ids: set[str] = set()
        completed_status_consumers: dict[str, set[str]] = {beat_id: set() for beat_id in beat_id_set}

        def validate_condition_refs(condition: BeatCondition | None, *, beat_id: str) -> None:
            def validate_condition_clue_ref(*refs: str | None) -> None:
                for clue_ref in refs:
                    if clue_ref is not None and clue_ref not in clue_refs:
                        raise ValueError(
                            f"scenario beat {beat_id} condition references unknown clue {clue_ref}"
                        )

            if condition is None:
                return
            for nested in condition.all_of:
                validate_condition_refs(nested, beat_id=beat_id)
            for nested in condition.any_of:
                validate_condition_refs(nested, beat_id=beat_id)
            if condition.clue_discovered is not None:
                validate_condition_clue_ref(
                    condition.clue_discovered.clue_id,
                    condition.clue_discovered.clue_title,
                )
            if condition.clue_state is not None:
                validate_condition_clue_ref(
                    condition.clue_state.clue_id,
                    condition.clue_state.clue_title,
                )
            if (
                condition.scene_is is not None
                and condition.scene_is.scene_id is not None
                and condition.scene_is.scene_id not in scene_id_set
            ):
                raise ValueError(
                    f"scenario beat {beat_id} condition references unknown scene {condition.scene_is.scene_id}"
                )
            if (
                condition.scene_is is not None
                and condition.scene_is.title is not None
                and condition.scene_is.title not in scene_title_set
            ):
                raise ValueError(
                    f"scenario beat {beat_id} condition references unknown scene title {condition.scene_is.title}"
                )
            if (
                condition.scene_is is not None
                and condition.scene_is.phase is not None
                and condition.scene_is.phase not in scene_phase_set
            ):
                raise ValueError(
                    f"scenario beat {beat_id} condition references unknown scene phase {condition.scene_is.phase}"
                )
            if condition.current_scene_in is not None:
                for scene_id in condition.current_scene_in.scene_ids:
                    if scene_id not in scene_id_set:
                        raise ValueError(
                            f"scenario beat {beat_id} condition references unknown scene {scene_id}"
                        )
                for scene_title in condition.current_scene_in.titles:
                    if scene_title not in scene_title_set:
                        raise ValueError(
                            f"scenario beat {beat_id} condition references unknown scene title {scene_title}"
                        )
                for scene_phase in condition.current_scene_in.phases:
                    if scene_phase not in scene_phase_set:
                        raise ValueError(
                            f"scenario beat {beat_id} condition references unknown scene phase {scene_phase}"
                        )
            if (
                condition.beat_status_is is not None
                and condition.beat_status_is.beat_id not in beat_id_set
            ):
                raise ValueError(
                    f"scenario beat {beat_id} condition references unknown beat {condition.beat_status_is.beat_id}"
                )
            if condition.clue_visible_to_actor is not None:
                validate_condition_clue_ref(
                    condition.clue_visible_to_actor.clue_id,
                    condition.clue_visible_to_actor.clue_title,
                )
            if condition.actor_owns_clue is not None:
                validate_condition_clue_ref(
                    condition.actor_owns_clue.clue_id,
                    condition.actor_owns_clue.clue_title,
                )

        for scene in self.scenes:
            for clue_ref in scene.linked_clue_ids:
                if clue_ref not in clue_refs:
                    raise ValueError(
                        f"scenario scene {scene.scene_id} references unknown clue {clue_ref}"
                    )
            for objective in scene.scene_objectives:
                if objective.objective_id in objective_ids:
                    raise ValueError(
                        f"scenario objective_id {objective.objective_id} must be unique"
                    )
                objective_ids.add(objective.objective_id)
                if objective.beat_id is not None and objective.beat_id not in beat_id_set:
                    raise ValueError(
                        f"scenario scene objective {objective.objective_id} references unknown beat {objective.beat_id}"
                    )
        for beat in self.beats:
            unlock_condition = beat.unlock_conditions or beat.unlock_when
            for source_beat_id in collect_condition_status_refs(
                unlock_condition,
                status=ScenarioBeatStatus.COMPLETED,
            ):
                completed_status_consumers[source_beat_id].add(beat.beat_id)
        for beat in self.beats:
            unlock_condition = beat.unlock_conditions or beat.unlock_when
            block_condition = beat.block_conditions or beat.block_when
            complete_condition = beat.complete_conditions or beat.complete_when

            validate_condition_refs(unlock_condition, beat_id=beat.beat_id)
            validate_condition_refs(block_condition, beat_id=beat.beat_id)
            validate_condition_refs(complete_condition, beat_id=beat.beat_id)
            for clue_ref in (*beat.required_clues, *beat.optional_clues):
                if clue_ref not in clue_refs:
                    raise ValueError(
                        f"scenario beat {beat.beat_id} references unknown clue {clue_ref}"
                    )
            for next_beat_id in beat.next_beats:
                if next_beat_id not in beat_id_set:
                    raise ValueError(f"scenario beat {beat.beat_id} references unknown next beat {next_beat_id}")
            for consequence in beat.consequences:
                for target_beat_id in (*consequence.unlock_beat_ids, *consequence.block_beat_ids):
                    if target_beat_id not in beat_id_set:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown beat {target_beat_id}"
                        )
                for clue_ref in consequence.activate_fail_forward_for_clue_ids:
                    if clue_ref not in clue_refs:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown clue {clue_ref}"
                        )
                for reveal_clue in consequence.reveal_clues:
                    reveal_ref = reveal_clue.clue_id or reveal_clue.clue_title or "unknown"
                    if reveal_ref not in clue_refs:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown clue {reveal_ref}"
                        )
                for reveal_scene in consequence.reveal_scenes:
                    reveal_scene_ref = reveal_scene.scene_id or reveal_scene.scene_ref or "unknown"
                    if reveal_scene_ref not in scene_id_set:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown scene {reveal_scene_ref}"
                        )
                for objective_mark in consequence.mark_scene_objectives_complete:
                    if objective_mark.beat_id is not None and objective_mark.beat_id not in beat_id_set:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown beat {objective_mark.beat_id}"
                        )
                    if objective_mark.scene_id is not None and objective_mark.scene_id not in scene_id_set:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown scene {objective_mark.scene_id}"
                        )
                    if objective_mark.objective_id is not None and objective_mark.objective_id not in objective_ids:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence references unknown objective {objective_mark.objective_id}"
                        )

            downstream_completion_dependents = set(beat.next_beats)
            downstream_completion_dependents.update(completed_status_consumers[beat.beat_id])
            for consequence in beat.consequences:
                downstream_completion_dependents.update(consequence.unlock_beat_ids)

            if complete_condition is None and downstream_completion_dependents:
                raise ValueError(
                    f"scenario beat {beat.beat_id} can never complete because downstream beat flow depends on it: "
                    + ", ".join(sorted(downstream_completion_dependents))
                )

            impossible_unlock_reason = analyze_unsatisfiable_scene_condition(unlock_condition)
            if impossible_unlock_reason is not None:
                raise ValueError(
                    f"scenario beat {beat.beat_id} unlock_conditions can never be satisfied: "
                    f"{impossible_unlock_reason}"
                )

            if (
                block_condition is not None
                and complete_condition is not None
                and normalize_condition_payload(block_condition)
                == normalize_condition_payload(complete_condition)
            ):
                raise ValueError(
                    f"scenario beat {beat.beat_id} block_conditions contradict complete_conditions"
                )

            impossible_complete_reason = analyze_unsatisfiable_complete_condition(
                complete_condition,
                beat_id=beat.beat_id,
            )
            if impossible_complete_reason is not None:
                raise ValueError(
                    f"scenario beat {beat.beat_id} complete_conditions can never be satisfied: "
                    f"{impossible_complete_reason}"
                )

        if not self.beats:
            return self

        beat_graph: dict[str, set[str]] = {beat_id: set() for beat_id in beat_id_set}
        entry_beats: set[str] = set()
        for beat in self.beats:
            if beat.start_unlocked:
                entry_beats.add(beat.beat_id)

            unlock_condition = beat.unlock_conditions or beat.unlock_when
            unlock_refs = collect_condition_beat_refs(unlock_condition)
            if unlock_condition is not None and not unlock_refs:
                entry_beats.add(beat.beat_id)
            for source_beat_id in unlock_refs:
                beat_graph[source_beat_id].add(beat.beat_id)

            for next_beat_id in beat.next_beats:
                if next_beat_id == beat.beat_id:
                    raise ValueError(
                        f"scenario beat {beat.beat_id} cannot reference itself via next_beats"
                    )
                beat_graph[beat.beat_id].add(next_beat_id)

            for consequence in beat.consequences:
                for target_beat_id in consequence.unlock_beat_ids:
                    if target_beat_id == beat.beat_id:
                        raise ValueError(
                            f"scenario beat {beat.beat_id} consequence cannot unlock itself"
                        )
                    beat_graph[beat.beat_id].add(target_beat_id)

        if not entry_beats:
            raise ValueError(
                "scenario beat graph has no entry beat; add start_unlocked or an unlock condition without beat dependencies"
            )

        cycle = find_cycle(beat_graph)
        if cycle is not None:
            raise ValueError(f"scenario beat graph contains cycle: {' -> '.join(cycle)}")

        reachable_beats: set[str] = set()
        pending = list(entry_beats)
        while pending:
            beat_id = pending.pop()
            if beat_id in reachable_beats:
                continue
            reachable_beats.add(beat_id)
            pending.extend(sorted(beat_graph[beat_id] - reachable_beats))
        unreachable_beats = [beat.beat_id for beat in self.beats if beat.beat_id not in reachable_beats]
        if unreachable_beats:
            raise ValueError(
                "scenario beat graph has unreachable beat(s): "
                + ", ".join(unreachable_beats)
            )
        return self


class SceneState(BaseModel):
    scene_id: str = Field(default_factory=lambda: f"scene-{uuid4().hex}")
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1)
    phase: str = Field(default="investigation", min_length=1, max_length=40)


class SessionEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"event-{uuid4().hex}")
    event_type: EventType
    actor_id: str | None = None
    actor_type: ActorType = ActorType.SYSTEM
    visibility_scope: VisibilityScope = VisibilityScope.PUBLIC
    visible_to: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    rules_grounding: "RuleGroundingSummary | None" = None
    is_authoritative: bool = True
    hallucination_flag: bool = False
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    created_at: datetime = Field(default_factory=utc_now)


class RuleGroundingSummary(BaseModel):
    query_text: str = Field(min_length=1)
    normalized_query: str | None = None
    matched_topics: list[str] = Field(default_factory=list)
    core_clue_flag: bool = False
    alternate_paths: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    deterministic_resolution_required: bool = False
    deterministic_handoff_topic: str | None = None
    conflicts_found: bool = False
    conflict_topics: list[str] = Field(default_factory=list)
    conflict_explanation: str | None = None
    human_review_recommended: bool = False
    human_review_reason: str | None = None
    chinese_answer_draft: str | None = None
    review_summary: str | None = None


class DraftAction(BaseModel):
    draft_id: str = Field(default_factory=lambda: f"draft-{uuid4().hex}")
    actor_id: str
    actor_type: ActorType
    visibility_scope: VisibilityScope
    visible_to: list[str] = Field(default_factory=list)
    draft_text: str = Field(min_length=1)
    structured_action: dict[str, Any] = Field(default_factory=dict)
    effects: ActionEffects = Field(default_factory=ActionEffects)
    effect_contract_origin: EffectContractOrigin = EffectContractOrigin.EXPLICIT
    risk_level: RiskLevel = RiskLevel.LOW
    core_clue_flag: bool = False
    affects_state: list[str] = Field(default_factory=list)
    requires_explicit_approval: bool = False
    rationale_summary: str = Field(default="待人工审核")
    rules_grounding: RuleGroundingSummary | None = None
    behavior_context: list["BehaviorPrecedent"] = Field(default_factory=list)
    supersedes_draft_id: str | None = None
    created_at_version: int = Field(default=1, ge=1)
    editable_fields: list[str] = Field(
        default_factory=lambda: ["draft_text", "structured_action", "rationale_summary"]
    )
    review_status: ReviewStatus = ReviewStatus.PENDING
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    created_at: datetime = Field(default_factory=utc_now)


class ReviewDecision(BaseModel):
    decision: ReviewDecisionType
    editor_notes: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class BehaviorPrecedent(BaseModel):
    precedent_id: str = Field(default_factory=lambda: f"precedent-{uuid4().hex}")
    actor_id: str
    source_review_id: str
    final_text: str = Field(min_length=1)
    final_structured_action: dict[str, Any] = Field(default_factory=dict)
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    created_at: datetime = Field(default_factory=utc_now)


class ReviewedAction(BaseModel):
    review_id: str = Field(default_factory=lambda: f"review-{uuid4().hex}")
    draft_id: str
    actor_id: str
    actor_type: ActorType
    visibility_scope: VisibilityScope
    visible_to: list[str] = Field(default_factory=list)
    review_status: ReviewStatus
    final_text: str = Field(min_length=1)
    final_structured_action: dict[str, Any] = Field(default_factory=dict)
    effects: ActionEffects = Field(default_factory=ActionEffects)
    effect_contract_origin: EffectContractOrigin = EffectContractOrigin.EXPLICIT
    rules_grounding: RuleGroundingSummary | None = None
    review_summary: str | None = None
    execution_summary: str | None = None
    applied_state_changes: list[str] = Field(default_factory=list)
    applied_effects: list[AppliedEffectRecord] = Field(default_factory=list)
    applied_beat_transitions: list[ScenarioBeatTransitionRecord] = Field(default_factory=list)
    learn_from_final: bool = True
    decision: ReviewDecision
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    authoritative_action_id: str | None = None
    canonical_event_id: str | None = None
    invalidated_by_rollback_version: int | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AuthoritativeAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"action-{uuid4().hex}")
    source_type: AuthoritativeActionSource
    actor_id: str
    actor_type: ActorType
    visibility_scope: VisibilityScope
    visible_to: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    structured_action: dict[str, Any] = Field(default_factory=dict)
    effects: ActionEffects = Field(default_factory=ActionEffects)
    effect_contract_origin: EffectContractOrigin = EffectContractOrigin.EXPLICIT
    applied_effects: list[AppliedEffectRecord] = Field(default_factory=list)
    applied_beat_transitions: list[ScenarioBeatTransitionRecord] = Field(default_factory=list)
    rules_grounding: RuleGroundingSummary | None = None
    review_summary: str | None = None
    execution_summary: str | None = None
    draft_id: str | None = None
    review_id: str | None = None
    canonical_event_id: str | None = None
    invalidated_by_rollback_version: int | None = None
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    created_at: datetime = Field(default_factory=utc_now)


class AuditLogEntry(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"audit-{uuid4().hex}")
    action: AuditActionType
    actor_id: str | None = None
    subject_id: str | None = None
    session_version: int = Field(ge=1)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SessionParticipant(BaseModel):
    actor_id: str
    display_name: str = Field(min_length=1, max_length=80)
    kind: ParticipantKind
    character: Character
    imported_character_source_id: str | None = None
    character_import_sync_policy: CharacterImportSyncPolicy = (
        CharacterImportSyncPolicy.INITIALIZE_IF_MISSING
    )
    secrets: CharacterSecrets = Field(default_factory=CharacterSecrets)
    suggestion_hooks: list[SuggestionHookMaterial] = Field(default_factory=list)


class SessionState(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    session_id: str = Field(default_factory=lambda: f"session-{uuid4().hex}")
    keeper_id: str = Field(min_length=1, max_length=80)
    keeper_name: str = Field(min_length=1, max_length=80)
    playtest_group: str | None = Field(default=None, max_length=80)
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    allow_test_mode_self_review: bool = False
    status: SessionStatus = SessionStatus.PLANNED
    scenario: ScenarioScaffold
    current_scene: SceneState
    participants: list[SessionParticipant]
    timeline: list[SessionEvent] = Field(default_factory=list)
    draft_actions: list[DraftAction] = Field(default_factory=list)
    reviewed_actions: list[ReviewedAction] = Field(default_factory=list)
    authoritative_actions: list[AuthoritativeAction] = Field(default_factory=list)
    character_states: dict[str, SessionCharacterState] = Field(default_factory=dict)
    combat_context: CombatContext | None = None
    progress_state: ScenarioProgressState = Field(default_factory=ScenarioProgressState)
    behavior_memory: dict[str, list[BehaviorPrecedent]] = Field(default_factory=dict)
    audit_log: list[AuditLogEntry] = Field(default_factory=list)
    state_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("playtest_group")
    @classmethod
    def normalize_playtest_group(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_participants(self) -> "SessionState":
        actor_ids = [participant.actor_id for participant in self.participants]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("participant actor_ids must be unique")
        human_count = sum(participant.kind == ParticipantKind.HUMAN for participant in self.participants)
        ai_count = sum(participant.kind == ParticipantKind.AI for participant in self.participants)
        if human_count < 1 or human_count > 4:
            raise ValueError("sessions require between 1 and 4 human investigators")
        if ai_count > 4:
            raise ValueError("sessions support at most 4 AI investigators")
        return self


class SessionParticipantSummary(BaseModel):
    actor_id: str
    display_name: str
    kind: ParticipantKind
    character: Character


class InvestigatorView(BaseModel):
    session_id: str
    viewer_id: str | None = None
    viewer_role: ViewerRole
    keeper_name: str
    language_preference: LanguagePreference
    scenario: ScenarioScaffold
    current_scene: SceneState
    participants: list[SessionParticipantSummary]
    visible_events: list[SessionEvent]
    visible_draft_actions: list[DraftAction]
    visible_reviewed_actions: list[ReviewedAction]
    visible_authoritative_actions: list[AuthoritativeAction] = Field(default_factory=list)
    own_private_state: CharacterSecrets | None = None
    own_character_state: SessionCharacterState | None = None
    visible_private_state_by_actor: dict[str, CharacterSecrets] = Field(default_factory=dict)
    visible_character_states_by_actor: dict[str, SessionCharacterState] = Field(default_factory=dict)
    combat_context: CombatContext | None = None
    behavior_memory_by_actor: dict[str, list[BehaviorPrecedent]] = Field(default_factory=dict)
    progress_state: ScenarioProgressState | None = None
    keeper_workflow: KeeperWorkflowState | None = None
    state_version: int
    updated_at: datetime


class SessionStartRequest(BaseModel):
    keeper_id: str | None = Field(default=None, min_length=1, max_length=80)
    keeper_name: str = Field(min_length=1, max_length=80)
    playtest_group: str | None = Field(default=None, max_length=80)
    language_preference: LanguagePreference | None = None
    allow_test_mode_self_review: bool = False
    scenario: ScenarioScaffold
    participants: list[SessionParticipant]

    @field_validator("playtest_group")
    @classmethod
    def normalize_playtest_group(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SessionStartResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    keeper_view: InvestigatorView


class PlayerActionRequest(BaseModel):
    actor_id: str
    action_text: str = Field(min_length=1)
    structured_action: dict[str, Any] = Field(default_factory=lambda: {"type": "free_text_action"})
    effects: ActionEffects | None = None
    rules_query_text: str | None = None
    deterministic_resolution_required: bool = False
    visibility_scope: VisibilityScope = VisibilityScope.PUBLIC
    visible_to: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None
    language_preference: LanguagePreference | None = None


class PlayerActionResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    grounding_degraded: bool = False
    authoritative_event: SessionEvent | None = None
    authoritative_action: AuthoritativeAction | None = None
    draft_action: DraftAction | None = None


class InvestigatorSkillCheckRequest(BaseModel):
    actor_id: str
    skill_name: str = Field(min_length=1, max_length=80)
    bonus_dice: int = Field(default=0, ge=0, le=2)
    penalty_dice: int = Field(default=0, ge=0, le=2)
    pushed: bool = False
    language_preference: LanguagePreference | None = None


class InvestigatorSkillCheckResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    skill_name: str
    skill_value: int = Field(ge=0, le=100)
    pushed: bool = False
    roll: D100Roll
    success: bool


class InvestigatorAttributeCheckRequest(BaseModel):
    actor_id: str
    attribute_name: str = Field(min_length=1, max_length=80)
    bonus_dice: int = Field(default=0, ge=0, le=2)
    penalty_dice: int = Field(default=0, ge=0, le=2)
    pushed: bool = False
    language_preference: LanguagePreference | None = None


class InvestigatorAttributeCheckResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    attribute_name: str
    attribute_value: int = Field(ge=1, le=99)
    pushed: bool = False
    roll: D100Roll
    success: bool


class InvestigatorOpposedCheckRequest(BaseModel):
    actor_id: str
    actor_label: str = Field(min_length=1, max_length=80)
    actor_target_value: int = Field(ge=1, le=100)
    opponent_label: str = Field(min_length=1, max_length=80)
    opponent_target_value: int = Field(ge=1, le=100)
    language_preference: LanguagePreference | None = None


class InvestigatorOpposedCheckResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    actor_label: str
    actor_target_value: int = Field(ge=1, le=100)
    opponent_label: str
    opponent_target_value: int = Field(ge=1, le=100)
    roll: D100Roll
    opponent_roll: D100Roll
    resolution: OpposedCheckResolution
    success: bool


class InvestigatorMeleeAttackRequest(BaseModel):
    actor_id: str
    target_actor_id: str = Field(min_length=1, max_length=80)
    attack_label: str = Field(min_length=1, max_length=80)
    attack_target_value: int = Field(ge=1, le=100)
    defense_mode: AttackDefenseMode
    defense_label: str = Field(min_length=1, max_length=80)
    defense_target_value: int = Field(ge=1, le=100)
    language_preference: LanguagePreference | None = None


class InvestigatorMeleeAttackResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    target_actor_id: str
    target_actor_name: str
    attack_label: str
    attack_target_value: int = Field(ge=1, le=100)
    defense_mode: AttackDefenseMode
    defense_label: str
    defense_target_value: int = Field(ge=1, le=100)
    roll: D100Roll
    defender_roll: D100Roll
    opposed_resolution: OpposedCheckResolution
    attack_resolution: AttackResolution
    success: bool


class InvestigatorRangedAttackRequest(BaseModel):
    actor_id: str
    target_actor_id: str = Field(min_length=1, max_length=80)
    attack_label: str = Field(min_length=1, max_length=80)
    attack_target_value: int = Field(ge=1, le=100)
    bonus_dice: int = Field(default=0, ge=0, le=2)
    penalty_dice: int = Field(default=0, ge=0, le=2)
    modifier_label: str = Field(default="normal", min_length=1, max_length=40)
    language_preference: LanguagePreference | None = None


class InvestigatorRangedAttackResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    target_actor_id: str
    target_actor_name: str
    attack_label: str
    attack_target_value: int = Field(ge=1, le=100)
    modifier_label: str
    roll: D100Roll
    attack_resolution: AttackResolution
    success: bool


class InvestigatorDamageResolutionRequest(BaseModel):
    actor_id: str
    target_actor_id: str = Field(min_length=1, max_length=80)
    damage_expression: str = Field(min_length=1, max_length=40)
    damage_bonus_expression: str | None = Field(default=None, max_length=40)
    armor_value: int = Field(default=0, ge=0, le=99)
    skip_hit_location: bool = False
    language_preference: LanguagePreference | None = None

    @field_validator("damage_expression", "damage_bonus_expression")
    @classmethod
    def _normalize_damage_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class InvestigatorDamageResolutionResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    target_actor_id: str
    target_actor_name: str
    damage_expression: str
    damage_bonus_expression: str | None = None
    armor_value: int = Field(ge=0, le=99)
    raw_damage: int = Field(ge=0)
    armor_absorbed: int = Field(ge=0)
    final_damage: int = Field(ge=0)
    hp_before: int = Field(ge=0)
    hp_after: int = Field(ge=0)
    hit_location_status: HitLocationStatus
    hit_location_roll: int | None = Field(default=None, ge=1, le=20)
    hit_location: HitLocation | None = None
    heavy_wound: bool = False
    heavy_wound_threshold: int = Field(ge=1)
    is_unconscious: bool = False
    is_dying: bool = False
    is_stable: bool = False
    rescue_window_open: bool = False
    death_confirmed: bool = False
    fatal_risk: bool = False
    kp_follow_up_required: bool = False


class InvestigatorFirstAidRequest(BaseModel):
    actor_id: str
    target_actor_id: str = Field(min_length=1, max_length=80)
    skill_name: str = Field(min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None


class InvestigatorFirstAidResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    target_actor_id: str
    target_actor_name: str
    skill_name: str
    skill_value: int = Field(ge=0, le=100)
    roll: D100Roll
    success: bool
    before_state_label: str
    after_state_label: str
    is_unconscious: bool = False
    is_dying: bool = False
    is_stable: bool = False
    rescue_window_open: bool = False
    death_confirmed: bool = False


class StartCombatContextRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    starting_actor_id: str | None = Field(default=None, min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None


class StartCombatContextResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    combat_context: CombatContext


class AdvanceCombatTurnRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None


class AdvanceCombatTurnResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    combat_context: CombatContext


class KeeperWoundResolutionRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    resolution: KeeperWoundResolution
    language_preference: LanguagePreference | None = None


class KeeperWoundResolutionResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    actor_id: str
    death_confirmed: bool = False
    is_unconscious: bool = False
    is_dying: bool = False
    is_stable: bool = False
    rescue_window_open: bool = False


class InvestigatorSanCheckRequest(BaseModel):
    actor_id: str
    source_label: str = Field(min_length=1, max_length=120)
    success_loss: str = Field(min_length=1, max_length=20)
    failure_loss: str = Field(min_length=1, max_length=20)
    language_preference: LanguagePreference | None = None


class InvestigatorSanCheckResponse(BaseModel):
    message: str
    session_id: str
    viewer_id: str
    state_version: int
    language_preference: LanguagePreference
    source_label: str
    previous_sanity: int = Field(ge=0, le=99)
    current_sanity: int = Field(ge=0, le=99)
    success_loss: str
    failure_loss: str
    applied_loss_expression: str
    resolved_sanity_loss: int = Field(ge=0)
    roll: D100Roll
    success: bool


class KPDraftRequest(BaseModel):
    draft_text: str = Field(min_length=1)
    structured_action: dict[str, Any] = Field(default_factory=lambda: {"type": "kp_note"})
    effects: ActionEffects | None = None
    rules_query_text: str | None = None
    deterministic_resolution_required: bool = False
    visibility_scope: VisibilityScope = VisibilityScope.KP_ONLY
    visible_to: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None
    language_preference: LanguagePreference | None = None


class ReviewDraftRequest(BaseModel):
    reviewer_id: str = Field(min_length=1, max_length=80)
    decision: ReviewDecisionType
    final_text: str | None = None
    final_structured_action: dict[str, Any] | None = None
    final_effects: ActionEffects | None = None
    editor_notes: str | None = None
    learn_from_final: bool = True
    language_preference: LanguagePreference | None = None
    regenerated_draft_text: str | None = None
    regenerated_structured_action: dict[str, Any] | None = None
    regenerated_effects: ActionEffects | None = None


class ReviewDraftResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    grounding_degraded: bool = False
    reviewed_action: ReviewedAction | None = None
    authoritative_action: AuthoritativeAction | None = None
    regenerated_draft: DraftAction | None = None


class ManualActionRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    actor_id: str | None = None
    actor_type: ActorType = ActorType.KEEPER
    action_text: str = Field(min_length=1)
    structured_action: dict[str, Any] = Field(default_factory=lambda: {"type": "manual_action"})
    effects: ActionEffects | None = None
    rules_query_text: str | None = None
    deterministic_resolution_required: bool = False
    visibility_scope: VisibilityScope = VisibilityScope.PUBLIC
    visible_to: list[str] = Field(default_factory=list)
    language_preference: LanguagePreference | None = None


class ApplyCharacterImportRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    actor_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1)
    refresh_existing: bool = True
    sync_policy: CharacterImportSyncPolicy | None = None
    force_apply_manual_review: bool = False
    language_preference: LanguagePreference | None = None


class ApplyCharacterImportResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    character_state: SessionCharacterState
    sync_report: CharacterImportSyncReport


class UpdateKeeperPromptRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    status: KeeperPromptStatus | None = None
    add_notes: list[str] = Field(default_factory=list)
    priority: KeeperPromptPriority | None = None
    assigned_to: str | None = None
    aftermath_label: str | None = Field(default=None, max_length=80)
    duration_rounds: int | None = Field(default=None, ge=1)
    language_preference: LanguagePreference | None = None

    @field_validator("aftermath_label")
    @classmethod
    def _normalize_aftermath_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("aftermath_label must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_changes_requested(self) -> "UpdateKeeperPromptRequest":
        if (
            self.status is None
            and not self.add_notes
            and self.priority is None
            and self.assigned_to is None
            and self.aftermath_label is None
            and self.duration_rounds is None
        ):
            raise ValueError("keeper prompt update requires at least one change")
        if self.assigned_to is not None and not self.assigned_to.strip():
            raise ValueError("keeper prompt assignment must not be blank")
        return self


class UpsertSuggestionHookRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    hook_label: str = Field(min_length=1, max_length=80)
    hook_text: str = Field(min_length=1, max_length=200)
    language_preference: LanguagePreference | None = None

    @field_validator("hook_label", "hook_text")
    @classmethod
    def _normalize_hook_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("hook fields must not be empty")
        return normalized


class SeedSuggestionHookRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None


class ImportCharacterHookSeedRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    occupation: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=200)
    seed_hint: str | None = Field(default=None, max_length=80)
    language_preference: LanguagePreference | None = None

    @field_validator("occupation", "notes", "seed_hint")
    @classmethod
    def _normalize_character_seed_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ImportSceneHookSeedRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=120)
    short_context: str = Field(min_length=1, max_length=200)
    seed_hint: str | None = Field(default=None, max_length=80)
    language_preference: LanguagePreference | None = None

    @field_validator("title", "short_context", "seed_hint")
    @classmethod
    def _normalize_scene_seed_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ImportTemplateCharacterHookRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=120)
    seed_hint: str | None = Field(default=None, max_length=80)
    language_preference: LanguagePreference | None = None

    @field_validator("source_id", "seed_hint")
    @classmethod
    def _normalize_template_hook_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UpdateKeeperPromptResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    prompt: QueuedKPPrompt
    keeper_workflow: KeeperWorkflowState


class KeeperLiveControlRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None


class UpdateSessionLifecycleRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=80)
    target_status: SessionStatus
    language_preference: LanguagePreference | None = None


class KeeperLiveControlResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    target_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)


class RollbackRequest(BaseModel):
    target_version: int = Field(ge=1)
    language_preference: LanguagePreference | None = None


class RollbackResponse(BaseModel):
    message: str
    session_id: str
    state_version: int
    language_preference: LanguagePreference
    current_view: InvestigatorView


class SessionImportWarning(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    ref: str | None = None
    source_id: str | None = None


class SessionImportResponse(BaseModel):
    original_session_id: str
    new_session_id: str
    state_version: int
    warnings: list[SessionImportWarning] = Field(default_factory=list)


class SessionCheckpointSummary(BaseModel):
    checkpoint_id: str = Field(min_length=1)
    source_session_id: str = Field(min_length=1)
    source_session_version: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=80)
    note: str | None = None
    created_at: datetime
    created_by: str | None = None


class SessionCheckpoint(SessionCheckpointSummary):
    snapshot_payload: dict[str, Any]


class CreateCheckpointRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    note: str | None = None
    operator_id: str | None = Field(default=None, min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None


class CreateCheckpointResponse(BaseModel):
    message: str
    session_id: str
    checkpoint: SessionCheckpointSummary


class UpdateCheckpointRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = None
    operator_id: str | None = Field(default=None, min_length=1, max_length=80)
    language_preference: LanguagePreference | None = None

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("label must not be empty")
        return normalized

    @field_validator("note")
    @classmethod
    def _normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class UpdateCheckpointResponse(BaseModel):
    message: str
    session_id: str
    checkpoint: SessionCheckpointSummary


class ListCheckpointsResponse(BaseModel):
    session_id: str
    checkpoints: list[SessionCheckpointSummary] = Field(default_factory=list)


class RestoreCheckpointRequest(BaseModel):
    language_preference: LanguagePreference | None = None


class RestoreCheckpointResponse(BaseModel):
    checkpoint_id: str
    source_session_id: str
    new_session_id: str
    state_version: int
    warnings: list[SessionImportWarning] = Field(default_factory=list)


class DeleteCheckpointResponse(BaseModel):
    message: str
    session_id: str
    checkpoint_id: str


class SessionCheckpointExportPayload(BaseModel):
    format_version: int = Field(default=1, ge=1)
    exported_at: datetime
    checkpoint: SessionCheckpoint

    @field_validator("format_version")
    @classmethod
    def _validate_format_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported checkpoint export format_version")
        return value


class ImportCheckpointResponse(BaseModel):
    message: str
    checkpoint: SessionCheckpointSummary
    original_checkpoint_id: str = Field(min_length=1)


SessionEvent.model_rebuild()
DraftAction.model_rebuild()
ReviewedAction.model_rebuild()
AuthoritativeAction.model_rebuild()
BeatCondition.model_rebuild()
BeatConsequence.model_rebuild()
SessionCharacterState.model_rebuild()
SessionState.model_rebuild()
InvestigatorView.model_rebuild()
