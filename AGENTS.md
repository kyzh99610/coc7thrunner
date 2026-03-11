# AGENTS.md

## Project purpose
Build a local-first Call of Cthulhu 7th edition semi-automated tabletop assistant.

## Primary goals
- 1-4 remote human investigators
- 0-4 AI investigators for testing
- deterministic rules engine
- secret isolation
- scenario generation
- interactive session progression
- logic maps
- optional local image generation on demand

## Non-negotiable rules
1. Never let LLM outputs override deterministic rules.
2. Never leak private investigator secrets across views.
3. All important generated outputs must be structured JSON first.
4. Prompts must live in files, not hidden inline strings unless trivial.
5. Domain logic must stay inside the domain layer.
6. Every rule-engine change must include tests.
7. Every secret-scoping change must include tests.
8. Image generation is optional and must be isolated from map truth.
9. Core clues must not be permanently missable from a single failed roll.
10. High-risk AI outputs must always require explicit human approval.
11. Low-risk actions may use simplified review flow, but cannot directly mutate authoritative history before finalization.
12. Human-edited approved outputs should be the default source for future behavior conditioning.

## Engineering style
- Python 3.12
- Type hints required
- Prefer explicit schemas
- Small functions
- Clear module boundaries
- Pure functions where possible
- Repository pattern for data access
- Replaceable model backends
- Log all important state transitions

## Architecture rules
- rules_engine = deterministic truth
- application layer = orchestration
- retrieval layer = grounding
- LLM layer = narrative and suggestions
- image_worker = optional asset generation only
- secrets_manager = per-user scoped visibility

## Priority order
1. skeleton repo
2. rules engine
3. schemas
4. secrets manager
5. session state machine
6. retrieval
7. character builder
8. scenario generator
9. AI investigator simulator
10. image worker

- secret isolation
- review gate
- core clue fail-forward
- deterministic rules
- session state consistency

## First implementation targets
1. create repo structure
2. implement API app boot
3. implement Character schema
4. implement Session schema
5. implement deterministic dice engine
6. implement secret-scoped message model
7. implement session state model
8. implement basic knowledge ingestion
9. implement /sessions/start
10. implement /sessions/{id}/player-action

## Required tests first
1. dice tests
2. character validation tests
3. secret leakage tests
4. session progression tests
5. rollback tests

## Output expectations for AI agents
When implementing:
- explain assumptions briefly
- write files directly
- keep TODOs explicit
- do not handwave missing code
- add tests with each domain change
- prefer working minimal code over grand abstractions

## Additional mandatory rules

### Human review gate
1. No AI-generated state-changing action is authoritative until reviewed by the human operator.
2. Store both draft and final versions.
3. Only the human-approved final version updates:
   - session history
   - character behavior memory
   - campaign state
   - clue ownership
   - future action tendencies
4. When a human edits an AI action, future AI behavior should condition on the edited version, not the discarded draft.
5. Support full manual override at any point.

### Chinese-first execution
1. Default user-facing language is Simplified Chinese.
2. All user-visible prompts, responses, action suggestions, and KP narration should support Chinese output by default.
3. Internal code, filenames, and schemas may remain English.
4. Do not hardcode English-only UX copy.
5. Add language fields to relevant schemas where user-visible content is generated.

## New priority items
1. review-gate workflow
2. Chinese-first user-visible execution
3. secret isolation
4. deterministic rules
5. session state consistency

## Required first-class entities to add
- DraftAction
- ReviewedAction
- ReviewDecision
- LanguagePreference
- VisibilityScope