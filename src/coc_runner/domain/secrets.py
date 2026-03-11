from __future__ import annotations

from coc_runner.domain.models import (
    BehaviorPrecedent,
    CharacterSecrets,
    InvestigatorView,
    KeeperPromptStatus,
    KeeperPromptPriority,
    KeeperWorkflowSummary,
    KeeperWorkflowState,
    SessionCharacterState,
    SessionParticipantSummary,
    SessionState,
    ViewerRole,
    VisibilityScope,
)


def _is_visible_to_viewer(
    *,
    visibility_scope: VisibilityScope,
    visible_to: list[str],
    viewer_id: str | None,
    viewer_role: ViewerRole,
) -> bool:
    if visibility_scope == VisibilityScope.SYSTEM_INTERNAL:
        return False
    if viewer_role == ViewerRole.KEEPER:
        return True
    if visibility_scope == VisibilityScope.PUBLIC:
        return True
    if viewer_id is None:
        return False
    return viewer_id in visible_to


def _is_active_keeper_prompt(status: KeeperPromptStatus) -> bool:
    return status in {KeeperPromptStatus.PENDING, KeeperPromptStatus.ACKNOWLEDGED}


def _priority_label(priority: KeeperPromptPriority) -> str:
    return {
        KeeperPromptPriority.LOW: "低",
        KeeperPromptPriority.MEDIUM: "中",
        KeeperPromptPriority.HIGH: "高",
    }[priority]


def _status_label(status: KeeperPromptStatus) -> str:
    return {
        KeeperPromptStatus.PENDING: "待处理",
        KeeperPromptStatus.ACKNOWLEDGED: "已确认",
        KeeperPromptStatus.DISMISSED: "已忽略",
        KeeperPromptStatus.COMPLETED: "已完成",
    }[status]


def _scene_label(session: SessionState, scene_id: str | None) -> str | None:
    if scene_id is None:
        return None
    scene = next((scene for scene in session.scenario.scenes if scene.scene_id == scene_id), None)
    if scene is None:
        return scene_id
    return f"{scene.title}（{scene_id}）"


def _beat_label(session: SessionState, beat_id: str | None) -> str | None:
    if beat_id is None:
        return None
    beat = next((beat for beat in session.scenario.beats if beat.beat_id == beat_id), None)
    if beat is None:
        return beat_id
    return f"{beat.title}（{beat_id}）"


def _build_prompt_lines(
    *,
    session: SessionState,
    active_prompts,
) -> list[str]:
    lines: list[str] = []
    for prompt in active_prompts:
        category = f" / {prompt.category}" if prompt.category else ""
        details: list[str] = []
        if prompt.assigned_to is not None:
            details.append(f"指派：{prompt.assigned_to}")
        if prompt.scene_id is not None:
            details.append(f"场景：{_scene_label(session, prompt.scene_id)}")
        if prompt.beat_id is not None:
            details.append(f"节点：{_beat_label(session, prompt.beat_id)}")
        if prompt.source_action_id is not None:
            details.append(f"动作：{prompt.source_action_id}")
        if prompt.trigger_reason is not None:
            details.append(f"原因：{prompt.trigger_reason}")
        if prompt.notes:
            details.append(f"备注：{' / '.join(prompt.notes)}")
        line = (
            f"{_priority_label(prompt.priority)}优先提示（{_status_label(prompt.status)}{category}）："
            f"{prompt.prompt_text}"
        )
        if details:
            line = f"{line}；" + "；".join(details)
        lines.append(line)
    return lines


def _build_objective_lines(
    *,
    session: SessionState,
    unresolved_objectives,
) -> list[str]:
    lines: list[str] = []
    for objective in unresolved_objectives:
        origin_label = "场景目标" if objective.origin.value == "scene" else "节点目标"
        details: list[str] = []
        if objective.scene_id is not None:
            details.append(f"场景：{_scene_label(session, objective.scene_id)}")
        if objective.beat_id is not None:
            details.append(f"节点：{_beat_label(session, objective.beat_id)}")
        if objective.source_action_id is not None:
            details.append(f"动作：{objective.source_action_id}")
        if objective.trigger_reason is not None:
            details.append(f"原因：{objective.trigger_reason}")
        line = f"待处理{origin_label}：{objective.text}"
        if details:
            line = f"{line}；" + "；".join(details)
        lines.append(line)
    return lines


def _build_completed_objective_lines(
    *,
    session: SessionState,
    recent_completed_objectives,
) -> list[str]:
    lines: list[str] = []
    for objective in recent_completed_objectives:
        details: list[str] = []
        if objective.scene_id is not None:
            details.append(f"场景：{_scene_label(session, objective.scene_id)}")
        if objective.beat_id is not None:
            details.append(f"节点：{_beat_label(session, objective.beat_id)}")
        if objective.source_action_id is not None:
            details.append(f"动作：{objective.source_action_id}")
        if objective.trigger_reason is not None:
            details.append(f"原因：{objective.trigger_reason}")
        line = f"最近完成目标：{objective.text}"
        if details:
            line = f"{line}；" + "；".join(details)
        lines.append(line)
    return lines


def _build_progression_lines(
    *,
    session: SessionState,
    recent_transitions,
) -> list[str]:
    lines: list[str] = []
    for transition in recent_transitions:
        details: list[str] = []
        beat_label = _beat_label(session, transition.beat_id)
        if beat_label is not None:
            details.append(f"节点：{beat_label}")
        if transition.trigger_action_id is not None:
            details.append(f"动作：{transition.trigger_action_id}")
        if transition.reason is not None:
            details.append(f"原因：{transition.reason}")
        line = f"最近推进：{transition.summary}"
        if details:
            line = f"{line}；" + "；".join(details)
        lines.append(line)
    return lines


def _build_keeper_summary_lines(
    *,
    prompt_lines: list[str],
    objective_lines: list[str],
    completed_objective_lines: list[str],
    progression_lines: list[str],
) -> list[str]:
    return [*prompt_lines, *objective_lines, *completed_objective_lines, *progression_lines]


def filter_session_for_viewer(
    session: SessionState,
    *,
    viewer_id: str | None,
    viewer_role: ViewerRole,
) -> InvestigatorView:
    if viewer_role == ViewerRole.INVESTIGATOR and viewer_id is None:
        raise ValueError("viewer_id is required for investigator views")

    visible_events = [
        event
        for event in session.timeline
        if _is_visible_to_viewer(
            visibility_scope=event.visibility_scope,
            visible_to=event.visible_to,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
    ]
    visible_draft_actions = [
        draft
        for draft in session.draft_actions
        if _is_visible_to_viewer(
            visibility_scope=draft.visibility_scope,
            visible_to=draft.visible_to,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
    ]
    visible_reviewed_actions = [
        reviewed
        for reviewed in session.reviewed_actions
        if _is_visible_to_viewer(
            visibility_scope=reviewed.visibility_scope,
            visible_to=reviewed.visible_to,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
    ]
    visible_authoritative_actions = [
        action
        for action in session.authoritative_actions
        if _is_visible_to_viewer(
            visibility_scope=action.visibility_scope,
            visible_to=action.visible_to,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
    ]
    visible_clues = [
        clue
        for clue in session.scenario.clues
        if _is_visible_to_viewer(
            visibility_scope=clue.visibility_scope,
            visible_to=clue.visible_to,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
    ]
    visible_clue_ids = {clue.clue_id for clue in visible_clues}
    visible_scenes = [
        scene
        for scene in session.scenario.scenes
        if scene.revealed
        and _is_visible_to_viewer(
            visibility_scope=scene.visibility_scope,
            visible_to=scene.visible_to,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
    ]

    participant_summaries = [
        SessionParticipantSummary(
            actor_id=participant.actor_id,
            display_name=participant.display_name,
            kind=participant.kind,
            character=participant.character,
        )
        for participant in session.participants
    ]

    own_private_state = None
    own_character_state = None
    visible_private_state_by_actor: dict[str, CharacterSecrets] = {}
    visible_character_states_by_actor: dict[str, SessionCharacterState] = {}
    behavior_memory_by_actor: dict[str, list[BehaviorPrecedent]] = {}
    keeper_workflow = None
    if viewer_role == ViewerRole.KEEPER:
        visible_private_state_by_actor = {
            participant.actor_id: participant.secrets for participant in session.participants
        }
        visible_character_states_by_actor = dict(session.character_states)
        behavior_memory_by_actor = session.behavior_memory
        active_prompts = [
            prompt.model_copy(deep=True)
            for prompt in session.progress_state.queued_kp_prompts
            if _is_active_keeper_prompt(prompt.status)
        ]
        unresolved_objectives = [
            objective.model_copy(deep=True)
            for objective in session.progress_state.active_scene_objectives
            if not objective.resolved
        ]
        recent_completed_objectives = [
            objective.model_copy(deep=True)
            for objective in reversed(session.progress_state.completed_objective_history[-5:])
        ]
        recent_transitions = [
            transition.model_copy(deep=True)
            for transition in reversed(session.progress_state.transition_history[-5:])
        ]
        prompt_lines = _build_prompt_lines(
            session=session,
            active_prompts=active_prompts,
        )
        objective_lines = _build_objective_lines(
            session=session,
            unresolved_objectives=unresolved_objectives,
        )
        completed_objective_lines = _build_completed_objective_lines(
            session=session,
            recent_completed_objectives=recent_completed_objectives,
        )
        progression_lines = _build_progression_lines(
            session=session,
            recent_transitions=recent_transitions,
        )
        keeper_workflow = KeeperWorkflowState(
            active_prompts=active_prompts,
            unresolved_objectives=unresolved_objectives,
            summary=KeeperWorkflowSummary(
                active_prompt_count=len(active_prompts),
                unresolved_objective_count=len(unresolved_objectives),
                recently_completed_objectives=recent_completed_objectives,
                recent_beat_transitions=recent_transitions,
                prompt_lines=prompt_lines,
                objective_lines=objective_lines,
                completed_objective_lines=completed_objective_lines,
                progression_lines=progression_lines,
                summary_lines=_build_keeper_summary_lines(
                    prompt_lines=prompt_lines,
                    objective_lines=objective_lines,
                    completed_objective_lines=completed_objective_lines,
                    progression_lines=progression_lines,
                ),
            ),
        )
    elif viewer_id is not None:
        for participant in session.participants:
            if participant.actor_id == viewer_id:
                own_private_state = participant.secrets
                break
        own_character_state = session.character_states.get(viewer_id)

    return InvestigatorView(
        session_id=session.session_id,
        viewer_id=viewer_id,
        viewer_role=viewer_role,
        keeper_name=session.keeper_name,
        language_preference=session.language_preference,
        scenario=session.scenario.model_copy(
            update={
                "clues": visible_clues,
                "beats": session.scenario.beats if viewer_role == ViewerRole.KEEPER else [],
                "scenes": (
                    session.scenario.scenes
                    if viewer_role == ViewerRole.KEEPER
                    else [
                        scene.model_copy(
                            update={
                                "linked_clue_ids": [
                                    clue_id for clue_id in scene.linked_clue_ids if clue_id in visible_clue_ids
                                ],
                                "scene_objectives": [],
                                "keeper_notes": [],
                                "runtime_notes": [],
                            }
                        )
                        for scene in visible_scenes
                    ]
                ),
                "npcs": session.scenario.npcs if viewer_role == ViewerRole.KEEPER else [],
            }
        ),
        current_scene=session.current_scene,
        participants=participant_summaries,
        visible_events=visible_events,
        visible_draft_actions=visible_draft_actions,
        visible_reviewed_actions=visible_reviewed_actions,
        visible_authoritative_actions=visible_authoritative_actions,
        own_private_state=own_private_state,
        own_character_state=own_character_state,
        visible_private_state_by_actor=visible_private_state_by_actor,
        visible_character_states_by_actor=visible_character_states_by_actor,
        behavior_memory_by_actor=behavior_memory_by_actor,
        progress_state=session.progress_state if viewer_role == ViewerRole.KEEPER else None,
        keeper_workflow=keeper_workflow,
        state_version=session.state_version,
        updated_at=session.updated_at,
    )
