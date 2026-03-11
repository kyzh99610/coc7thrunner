# Scenario Authoring Contract

This file defines the current lightweight authoring contract for playable CoC scenarios in the MVP backend.

## Goals

- Keep beat and clue references stable across ingestion, execution, and rollback.
- Make fail-forward explicit for core clues.
- Keep progression data machine-readable so authoritative execution can audit it.

## ID Conventions

- `beat_id`: use stable ASCII ids with a scenario-scoped prefix, for example `hotel.office_search` or `hotel.hidden_room_entry`.
- `clue_id`: use stable ASCII ids with a scenario-scoped prefix, for example `hotel.ledger_page`.
- Do not use generated UI text as the only durable reference when authoring scenarios.
- Titles can still be used for display, but ids should be the canonical authoring reference.

## Clues

- `required_clues`: clues that must be available for the beat to unlock or complete.
- `optional_clues`: clues that enrich the beat, but should not hard-block progression.
- `alternate_paths`: required for core clues unless `fail_forward_text` is present.
- `core_clue_flag`: use this when the clue must never become a single-point failure.

Authoring rule:
- Prefer clue ids in `required_clues`, `optional_clues`, and consequence declarations.
- Titles are still accepted for compatibility, but ids are safer.

## Beats

Each beat should define:

- `beat_id`
- `title`
- `scene_objective`
- `required_clues`
- `optional_clues`
- `unlock_conditions`
- `complete_conditions`
- `consequences`
- `next_beats`

Authoring guideline:
- Keep one player-facing objective per beat.
- Use `next_beats` for the most common progression path.
- Use explicit consequences when progression should also mutate clue, scene, note, NPC, or KP prompt state.
- Use `scene_objective` as a fallback workflow item only when no authored scene objective already tracks that beat.

## Scenes

Use `scenes` as the first-class registry for location/stateful progression data.

Each scene can define:

- `scene_id`
- `title`
- `summary`
- `revealed`
- `linked_clue_ids`
- `scene_objectives`
- `keeper_notes`

Authoring guideline:
- Use stable `scene_id` values in scene transitions and reveal-scene consequences.
- Put keeper-facing location objectives on the scene whenever the task is tied to a place or phase of play.
- Use scene objectives for workflow items the KP should actively track.
- Reserve `beat.scene_objective` for portable beat-level decisions that do not already belong to a scene-owned objective.

## Conditions

Current supported condition leaves:

- `clue_discovered`
- `clue_state`
- `scene_is`
- `current_scene_in`
- `actor_has_status`
- `any_actor_has_status`
- `clue_visible_to_actor`
- `actor_owns_clue`
- `beat_status_is`
- `deterministic_handoff_topic_matches`

Composition:

- `all_of`
- `any_of`

Authoring guideline:
- Prefer `scene_is.scene_id` and `current_scene_in.scene_ids` over title-based checks.
- Title-based scene checks remain available for compatibility, but new authored scenarios should use stable ids.

## Consequences

Current supported consequence declarations:

- `unlock_beat_ids`
- `block_beat_ids`
- `activate_fail_forward_for_clue_ids`
- `reveal_clues`
- `reveal_scenes`
- `apply_statuses`
- `npc_attitude_updates`
- `grant_private_notes`
- `queue_kp_prompts`
- `mark_scene_objectives_complete`

Authoring guideline:

- Use `reveal_clues` when a completed beat should explicitly expose a clue.
- Use `apply_statuses` and `grant_private_notes` for actor-scoped fallout.
- Use `queue_kp_prompts` for keeper-facing follow-up, not player-facing narration.
- `queue_kp_prompts` may now declare `priority` and `assigned_to` for lightweight keeper workflow routing.
- Use `mark_scene_objectives_complete` explicitly instead of assuming beat completion implies objective completion.

## Keeper Workflow

Keeper-facing workflow is currently derived from:

- active KP prompts with lifecycle states
- unresolved scene/beat objectives
- progression transition history

Each workflow item should keep enough references to answer:

- what triggered it
- which beat it relates to
- which scene it belongs to
- which authoritative action created it

Prompt lifecycle:

- `pending`: newly queued and not yet handled
- `acknowledged`: keeper has seen it and intends to act on it
- `dismissed`: keeper explicitly chose not to pursue it
- `completed`: keeper handled the prompt and no longer needs it in the active queue

Only `pending` and `acknowledged` prompts should appear in the active keeper workflow queue.

Prompt polish:

- `priority`: `low`, `medium`, or `high`
- `assigned_to`: optional lightweight ownership field for keeper/operator follow-up
- `notes`: runtime-only keeper notes attached during play; not part of authored scenario declarations

## Fail-Forward

- Core clues must always provide `alternate_paths` or `fail_forward_text`.
- If a beat depends on a core clue, author either:
  - an alternate discovery route, or
  - an explicit fail-forward activation path.
- Do not author a beat that can only progress through one roll with no fallback.

## Auditability

Beat transitions are recorded with:

- `beat_id`
- transition type
- triggering authoritative `action_id`
- reason
- fired condition refs
- fired consequence refs

This is the current keeper-facing progression audit trail.

## Naming Compatibility

- Canonical runtime field: `completed_objectives`
- Legacy compatibility mirror: `completed_scene_objectives`

Migration guidance:

- New code and debugging tools should read `completed_objectives`.
- Existing clients may continue reading `completed_scene_objectives` during migration.
- The backend currently keeps both fields mirrored until the old name can be removed safely.

## Current Limits

- NPC attitude is currently stored as simple keyed runtime state, not a full NPC model.
- KP prompt workflow is still lightweight. It supports lifecycle state, but not richer assignment, note-taking, or due-date semantics yet.
- Authoring still assumes the keeper writes stable ids and consequence refs carefully.
