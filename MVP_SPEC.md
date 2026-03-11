# CoC Runner MVP Spec

## Project name
CoC Runner

## Goal
Build a local-first, text-first, semi-automated Call of Cthulhu 7th edition tabletop assistant that supports:
- 1 to 4 remote human investigators
- optional AI investigators for pre-release testing
- deterministic rule enforcement
- private information / secret isolation per investigator
- scenario generation and interactive session progression
- optional local image generation for portraits and background art
- logic maps separated from visual art maps

## Product stance
This is NOT a fully autonomous god-tier Keeper.
This IS a controllable tabletop operating system for Call of Cthulhu 7e with:
- deterministic rules engine
- structured knowledge retrieval
- KP override controls
- optional automation toggles

## MVP features
1. Local-first backend
2. Text-first multiplayer session
3. 1-4 remote human players via browser
4. 0-4 AI investigators for automated testing
5. Per-user secret state isolation
6. Rulebook ingestion and retrieval
7. Character creation under rule constraints
8. Scenario generation with structured output
9. Interactive session progression
10. Logic map generation
11. Optional local portrait/background generation
12. Save/load/rollback for sessions

## Non-goals for MVP
1. No voice input
2. No TTS
3. No real-time audio chat
4. No fully automatic visual battlemap generation with exact object placement
5. No "LLM decides rules by itself"
6. No fancy animation pipeline
7. No mobile app
8. No external SaaS dependency in the hot path

## Core design rules
1. Deterministic rules must be implemented in code, not delegated to LLMs.
2. LLMs may propose narrative, choices, descriptions, and structured plans, but never final rules truth.
3. All major generated outputs must be JSON-first.
4. Secrets must be scoped per investigator and never leaked by default.
5. Maps must be two-layer:
   - logic layer: rooms, doors, clues, locks, routes, NPC positions
   - visual layer: optional background art / atmosphere art
6. AI investigators exist for testing and simulation, not as the primary release target.
7. KP can override any automated decision.

- Core clues must never be permanently locked behind a single failed roll.
- Low-risk actions may use simplified review UX, but still require an authoritative final version before state mutation.
- High-risk actions must always require explicit human approval.
- Human-edited approved actions should become the default basis for future behavior adaptation.

## Investigation design policy
1. Core clues must have multiple reachable paths.
2. Failed investigation rolls should usually reduce quality, speed, safety, or completeness rather than hard-stop progress.
3. Social actions should not erase NPC motivation.
4. Narrative convenience must not override deterministic truth.

## Human review gate (mandatory)
All AI-generated final actions must pass through a human review gate before becoming authoritative.

This applies to:
- AI Keeper narration
- AI Keeper scene progression
- AI investigator proposed actions
- AI investigator private reasoning summary intended to become action
- AI-generated clue delivery
- AI-generated state-changing narrative text

Required behavior:
1. AI first produces a draft action package.
2. The human operator (KP / host) can:
   - approve as-is
   - edit text
   - edit chosen action
   - replace the action entirely
   - reject and request regeneration
3. Only the approved/edited version becomes canonical session history.
4. All downstream memory, behavior modeling, and future state updates must use the approved/edited version, not the original AI draft.
5. AI behavior adaptation must learn from approved edits over time within the campaign/session context.
6. There must be an OFF toggle to disable automation and use pure manual control.

## Review gate data model
Each AI action should have:
- draft_id
- actor_id
- actor_type (keeper | investigator | npc | system)
- visibility_scope
- draft_text
- structured_action
- rationale_summary
- editable_fields
- review_status (pending | approved | edited | rejected | regenerated)
- final_text
- final_structured_action
- approved_by
- approved_at

Only final_text and final_structured_action are authoritative.

## Language policy
The product must support Chinese-first execution.

MVP language requirements:
1. Default UI language: Simplified Chinese
2. Default narrative output language: Simplified Chinese
3. Default character generation language: Simplified Chinese
4. Default scenario generation language: Simplified Chinese
5. Rules retrieval may use multilingual sources, but the user-facing result should default to Simplified Chinese
6. Internal schemas and code identifiers may remain English for maintainability
7. All prompts must support explicit language control
8. The system should preserve original proper nouns where appropriate, but present explanations in Chinese
9. Language selection must be configurable per campaign/session

## Chinese-first product stance
This product is intended to be fully usable in Chinese for the main user experience, including:
- UI labels
- KP controls
- session narration
- character generation
- scenario generation
- clue presentation
- investigator suggestions
- help text
- save/load metadata where user-facing

English may still be used internally for:
- code
- schemas
- developer docs
- internal identifiers

## User modes
### Keeper mode
- Manual KP: system only suggests
- Assisted KP: system drafts responses and updates state
- Auto KP: system can progress scenes, but KP can override

### Investigator mode
- Human-only
- Suggestion mode
- Auto investigator mode
- Mixed mode

### Ending mode
- fixed ending
- branching ending
- open ending constrained by state machine

## Secrets model
Each investigator has:
- public state
- private state
- hidden notes
- personal clues
- personal goals
- secret flags
- knowledge history

The system must support:
- public broadcast message
- private investigator message
- KP-only note
- shared clue
- hidden clue
- hallucination / unreliable perception flag

## MVP architecture
### Frontend
- React + TypeScript + Vite
- Text chat UI
- Session timeline
- Character sheet panel
- Secret panel
- KP control panel
- Logic map viewer
- Settings page

### Backend
- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy
- Alembic
- SQLite first, PostgreSQL optional later

### Local model layer
- one local text model service, always-on if possible
- one optional image worker, loaded on demand and unloaded immediately after generation
- model backend abstraction required

### Retrieval
- local embeddings
- Qdrant vector store
- chunked rulebook ingestion
- source-aware retrieval
- priority resolution:
  1. campaign house rules
  2. module-specific rules
  3. CoC 7 core rules
  4. other support material
  5. LLM guess

## Local image generation policy
Use local image generation only for:
- investigator portraits
- NPC portraits
- monster portraits
- item illustrations
- scene background art

Do NOT use image generation for:
- exact map truth
- room adjacency logic
- door and lock logic
- precise object placement
- authoritative NPC positions

All exact tactical/logic information must be represented separately in JSON/SVG/PDF logic maps.

## AI investigator testing goals
AI investigators should be able to simulate:
- consistent persona behavior
- private knowledge boundaries
- partial information decisions
- irrational but in-character decisions
- stress testing of edge-case branches

Three test modes:
1. Scripted tester
2. Persona tester
3. Chaos tester

## Core entities
- Character
- CharacterSecrets
- Campaign
- Scenario
- Session
- Scene
- NPC
- Clue
- Item
- Location
- LogicMap
- SessionEvent
- InvestigatorView
- KnowledgeChunk
- RuleResolution

## Required modules
1. rules_engine
2. character_builder
3. scenario_generator
4. session_director
5. secrets_manager
6. knowledge_ingest
7. knowledge_retrieval
8. map_logic_builder
9. image_worker
10. save_load_manager
11. ai_investigator_simulator

## Required API surface
- POST /characters/generate
- POST /characters/validate
- GET /characters/{id}
- POST /scenarios/generate
- GET /scenarios/{id}
- POST /sessions/start
- POST /sessions/{id}/player-action
- GET /sessions/{id}/state
- POST /sessions/{id}/rollback
- POST /rules/query
- POST /knowledge/ingest
- POST /maps/generate-logic
- POST /images/generate-portrait
- POST /images/generate-background
- POST /test/ai-investigator-step

## Save/load requirements
Must support:
- full campaign snapshot
- session rollback
- scene checkpoint
- reproducible rule results
- event log replay where possible

## Testing priorities
1. Rule correctness
2. Secret isolation
3. Scenario state consistency
4. Character validity
5. Session rollback correctness
6. AI investigator leakage checks
7. Retrieval grounding checks

## MVP success criteria
The MVP is successful if:
1. A KP can host a text session locally.
2. Up to 4 human investigators can connect remotely via browser.
3. Up to 4 AI investigators can simulate player actions for testing.
4. Character creation respects constraints.
5. Secret information remains isolated correctly.
6. The session can progress interactively without state corruption.
7. The system can save, reload, and rollback.
8. Portraits/backgrounds can be generated on demand without breaking the text runtime.