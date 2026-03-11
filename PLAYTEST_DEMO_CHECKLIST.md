# Playtest Demo Checklist

This is the shortest keeper-facing demo path for a controlled playtest of the CoC Runner MVP backend.

## Recommended Scenario

- Start with one of the small authored scenarios:
  - `scenario.whispering_guesthouse`
  - `scenario.midnight_archive`
  - `scenario.blackout_clinic`

## Demo Flow

1. Import a character
- Use `POST /knowledge/import-character-sheet` for a JSON, CSV, or integrated workbook sample.
- Confirm the import review block reports:
  - `template_profile_used`
  - `manual_review_required`
  - `warnings`

2. Apply the imported character to a session participant
- Use `POST /sessions/{session_id}/apply-character-import`.
- Confirm the session character state initializes:
  - current `hp/mp/san`
  - core stat baseline
  - skill baseline
  - import review flags

3. Load an authored scenario
- Use one of the authored payload helpers from `src/coc_runner/domain/scenario_examples.py`.
- Confirm the start scene is revealed and the initial keeper workflow has at least one unresolved objective.

4. Start the session
- Use `POST /sessions/start`.
- Check keeper state with `GET /sessions/{session_id}/state?viewer_role=keeper`.
- Confirm:
  - `current_scene`
  - `keeper_workflow.active_prompts`
  - `keeper_workflow.unresolved_objectives`
  - `keeper_workflow.summary`

5. Submit a grounded player action
- Use `POST /sessions/{session_id}/player-action`.
- Include:
  - `rules_query_text`
  - `deterministic_resolution_required` when the action should ground through rules
- Confirm the authoritative action records:
  - rules grounding
  - citations
  - applied effects
  - applied beat transitions

6. Submit and review one AI or KP draft
- Use `POST /sessions/{session_id}/player-action` with an AI participant or `POST /sessions/{session_id}/kp-draft`.
- Review with `POST /sessions/{session_id}/draft-actions/{draft_id}/review`.
- Confirm no draft mutates authoritative state before approval.

7. Observe progression changes
- After approval, check keeper state again.
- Confirm these changed as expected:
  - clue visibility / ownership
  - revealed scenes
  - unresolved and completed objectives
  - keeper prompts
  - recent beat transitions

8. Exercise keeper prompt workflow
- Use `POST /sessions/{session_id}/keeper-prompts/{prompt_id}/status`.
- Try:
  - `acknowledged`
  - `completed`
  - `dismissed`
- Optionally attach `add_notes`, `priority`, or `assigned_to`.
- Confirm the keeper summary updates coherently.

9. Test rollback
- Use `POST /sessions/{session_id}/rollback`.
- Confirm the keeper view returns to the prior state snapshot and that:
  - authoritative history reverts
  - clue / scene / objective / prompt state reverts
  - beat progression reverts

## What To Watch During The Demo

- Keeper summary should read clearly without scanning raw progress state.
- Prompts should show status, priority, assignment, notes, and related scene / beat / action refs.
- Core clues should remain fail-forward safe.
- Deterministic handoff topics should block unsafe execution mismatches.
- Reviewed actions should be the only draft-origin path that mutates canonical state.

## Suggested Demo Exit Criteria

- One imported character successfully initializes session state.
- One grounded human action progresses the scenario.
- One reviewed draft becomes authoritative.
- One keeper prompt is acknowledged or completed.
- One rollback restores the previous play state cleanly.
