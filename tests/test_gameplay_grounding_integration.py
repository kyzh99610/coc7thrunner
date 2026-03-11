from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coc_runner.application.session_service import SessionService
from tests.helpers import make_participant, make_scenario


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "knowledge"


def test_resolve_rules_query_text_auto_fallback_uses_terms_and_strips_fillers() -> None:
    assert (
        SessionService._resolve_rules_query_text(
            None,
            "我查看门边的脚印",
            {},
        )
        == "侦查"
    )
    assert (
        SessionService._resolve_rules_query_text(
            None,
            "我去图书馆查阅旧报纸",
            {},
        )
        == "图书馆使用"
    )
    assert (
        SessionService._resolve_rules_query_text(
            None,
            "我尝试说服旅店老板",
            {},
        )
        == "说服"
    )
    assert SessionService._resolve_rules_query_text(
        None,
        "好的",
        {},
    ) is None


@pytest.mark.parametrize(
    ("action_text", "expected_query"),
    [
        ("我走到窗户旁边坐了下来", None),
        ("我去看", None),
        ("嗯嗯", None),
        ("收到，先这样吧", None),
        ("我先去休息一下", None),
    ],
)
def test_resolve_rules_query_text_auto_fallback_returns_none_for_negative_or_too_short_text(
    action_text: str,
    expected_query: str | None,
) -> None:
    assert SessionService._resolve_rules_query_text(
        None,
        action_text,
        {},
    ) is expected_query


def test_player_action_auto_grounding_uses_action_text_when_query_missing(
    client: TestClient,
) -> None:
    _register_text_source(
        client,
        source_id="auto-grounding-core",
        source_kind="rulebook",
        source_title_zh="自动命中核心规则",
        default_priority=45,
    )
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "auto-grounding-core",
            "content": "# 图书馆使用\n图书馆使用用于在旧报纸、档案与馆藏中查阅资料。",
        },
    )
    assert ingest_response.status_code == 200

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我去图书馆查阅旧报纸。",
            "structured_action": {"type": "research"},
            "deterministic_resolution_required": True,
        },
    )
    assert action_response.status_code == 202

    grounding = action_response.json()["authoritative_event"]["rules_grounding"]
    assert grounding["query_text"] == "图书馆使用"
    assert grounding["deterministic_handoff_topic"] == "term:library_use"
    assert grounding["citations"]


def _register_text_source(
    client: TestClient,
    *,
    source_id: str,
    source_kind: str,
    source_title_zh: str,
    default_priority: int,
) -> None:
    response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": "markdown",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
            "default_priority": default_priority,
            "is_authoritative": True,
        },
    )
    assert response.status_code == 201


def _build_participant_from_extraction(
    extraction: dict,
    *,
    actor_id: str,
    kind: str = "human",
) -> dict:
    return {
        "actor_id": actor_id,
        "display_name": extraction["investigator_name"],
        "kind": kind,
        "character": {
            "name": extraction["investigator_name"],
            "occupation": extraction.get("occupation") or "调查员",
            "age": 30,
            "language_preference": "zh-CN",
            "attributes": {
                "strength": extraction["core_stats"]["strength"],
                "constitution": extraction["core_stats"]["constitution"],
                "size": extraction["core_stats"]["size"],
                "dexterity": extraction["core_stats"]["dexterity"],
                "appearance": extraction["core_stats"]["appearance"],
                "intelligence": extraction["core_stats"]["intelligence"],
                "power": extraction["core_stats"]["power"],
                "education": extraction["core_stats"]["education"],
            },
            "skills": extraction["skills"],
            "notes": extraction.get("campaign_notes"),
        },
        "secrets": {
            "private_notes": [f"{extraction['investigator_name']} 的私人笔记"],
            "personal_clues": [],
            "personal_goals": [],
            "hidden_flags": [],
            "knowledge_history": [],
        },
    }


def test_gameplay_smoke_flow_uses_grounded_rules_and_review_gate(client: TestClient) -> None:
    _register_text_source(
        client,
        source_id="gameplay-core",
        source_kind="rulebook",
        source_title_zh="游戏流程核心规则",
        default_priority=30,
    )
    _register_text_source(
        client,
        source_id="gameplay-house",
        source_kind="house_rule",
        source_title_zh="游戏流程房规",
        default_priority=80,
    )

    core_ingest = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "gameplay-core",
            "content": (
                "# 图书馆使用\n"
                "图书馆使用用于在旧报纸、档案与馆藏中查阅资料。\n\n"
                "# 侦查\n"
                "侦查用于发现隐藏线索与细微异常。"
            ),
        },
    )
    house_ingest = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "gameplay-house",
            "content": (
                "# 房规优先\n"
                "同一主题下，房规优先于官方规则。\n\n"
                "# 侦查\n"
                "房规：侦查失败时仍可获得核心线索，但会额外消耗时间并提高后续风险。"
            ),
        },
    )
    assert core_ingest.status_code == 200
    assert house_ingest.status_code == 200

    register_sheet = client.post(
        "/knowledge/register-source",
        json={
            "source_id": "gameplay-sheet",
            "source_kind": "character_sheet",
            "source_format": "xlsx",
            "source_title_zh": "许明角色卡",
            "document_identity": "gameplay-sheet",
            "source_path": str(FIXTURE_DIR / "character_sheet_sample.xlsx"),
            "default_priority": 0,
            "is_authoritative": False,
        },
    )
    assert register_sheet.status_code == 201

    import_sheet = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "gameplay-sheet"},
    )
    assert import_sheet.status_code == 200
    extraction = import_sheet.json()["extraction"]

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "报纸剪报",
                        "text": "核心线索藏在旧报纸的失踪案版面里。",
                        "core_clue_flag": True,
                        "fail_forward_text": "即使检定失败，调查员也会在花费更多时间后找到这则剪报。",
                        "visibility_scope": "public",
                    }
                ]
            ),
            "participants": [
                _build_participant_from_extraction(extraction, actor_id="investigator-1"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    player_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我查阅旧报纸寻找矿镇失踪案线索。",
            "structured_action": {"type": "research", "target": "old_newspapers"},
            "rules_query_text": "查阅旧报纸该用什么技能",
            "deterministic_resolution_required": True,
        },
    )
    assert player_action.status_code == 202
    player_payload = player_action.json()

    assert player_payload["authoritative_event"]["rules_grounding"]["deterministic_resolution_required"] is True
    assert player_payload["authoritative_event"]["rules_grounding"]["deterministic_handoff_topic"] == "term:library_use"
    assert player_payload["authoritative_event"]["rules_grounding"]["citations"]
    assert player_payload["authoritative_event"]["structured_payload"]["rules_grounding"]["citations"]

    ai_draft = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议继续侦查，即使失败也接受房规代价。",
            "structured_action": {"type": "suggest_action", "target": "warehouse"},
            "rules_query_text": "侦查失败时房规怎么处理",
            "deterministic_resolution_required": True,
        },
    )
    assert ai_draft.status_code == 202
    ai_payload = ai_draft.json()["draft_action"]

    assert ai_payload["review_status"] == "pending"
    assert ai_payload["rules_grounding"]["deterministic_handoff_topic"] == "term:spot_hidden"
    assert ai_payload["rules_grounding"]["citations"]
    assert "规则依据" in ai_payload["rationale_summary"]

    keeper_before_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert all(
        event["text"] != "我建议继续侦查，即使失败也接受房规代价。"
        for event in keeper_before_review["visible_events"]
    )

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{ai_payload['draft_id']}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 200
    reviewed = review_response.json()["reviewed_action"]

    assert reviewed["rules_grounding"]["deterministic_handoff_topic"] == "term:spot_hidden"
    assert reviewed["review_summary"] is not None
    assert "引用：" in reviewed["review_summary"]

    keeper_after_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    reviewed_events = [
        event
        for event in keeper_after_review["visible_events"]
        if event["event_type"] == "reviewed_action"
    ]

    assert any(event["text"] == "我建议继续侦查，即使失败也接受房规代价。" for event in reviewed_events)
    assert reviewed_events[-1]["rules_grounding"]["citations"]
    assert reviewed_events[-1]["structured_payload"]["rules_grounding"]["citations"]


def test_core_clue_contract_requires_fail_forward_support(client: TestClient) -> None:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "唯一核心线索",
                        "text": "这条线索不能单点永久错失。",
                        "core_clue_flag": True,
                        "visibility_scope": "public",
                    }
                ]
            ),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )

    assert response.status_code == 422
    assert "alternate_paths or fail_forward_text" in str(response.json()["detail"])


def test_scenario_clue_visibility_is_filtered_per_viewer(client: TestClient) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "公开线索",
                        "text": "所有调查员都知道旅店前台有访客登记簿。",
                        "visibility_scope": "public",
                    },
                    {
                        "title": "共享线索",
                        "text": "只有林舟被告知地下室入口在酒窖木架后。",
                        "visibility_scope": "shared_subset",
                        "visible_to": ["investigator-1"],
                    },
                    {
                        "title": "KP真相",
                        "text": "祭坛下方有一条通往密室的暗道。",
                        "visibility_scope": "kp_only",
                    },
                ]
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("investigator-2", "周岚"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    actor_one = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    actor_two = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()
    keeper = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()

    actor_one_clues = {clue["title"] for clue in actor_one["scenario"]["clues"]}
    actor_two_clues = {clue["title"] for clue in actor_two["scenario"]["clues"]}
    keeper_clues = {clue["title"] for clue in keeper["scenario"]["clues"]}

    assert actor_one_clues == {"公开线索", "共享线索"}
    assert actor_two_clues == {"公开线索"}
    assert keeper_clues == {"公开线索", "共享线索", "KP真相"}


def test_grounded_ai_draft_does_not_bypass_review_gate(client: TestClient) -> None:
    _register_text_source(
        client,
        source_id="gate-core",
        source_kind="rulebook",
        source_title_zh="审核门规则",
        default_priority=40,
    )
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "gate-core",
            "content": "# 推动检定\n推动检定失败时会触发更严重的后果。",
        },
    )
    assert ingest_response.status_code == 200

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议推动检定，即使失败也承担更严重的后果。",
            "structured_action": {"type": "suggest_action", "target": "sealed_door"},
            "rules_query_text": "推动检定失败后怎么处理",
            "deterministic_resolution_required": True,
        },
    )
    assert draft_response.status_code == 202
    draft_payload = draft_response.json()["draft_action"]

    assert draft_payload["review_status"] == "pending"
    assert draft_payload["rules_grounding"]["deterministic_handoff_topic"] == "term:pushed_roll"

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    assert all(
        event["text"] != "我建议推动检定，即使失败也承担更严重的后果。"
        for event in keeper_state["visible_events"]
    )
    assert any(
        draft["draft_id"] == draft_payload["draft_id"]
        for draft in keeper_state["visible_draft_actions"]
    )


def test_approved_action_execution_updates_scene_clue_and_character_state(
    client: TestClient,
) -> None:
    _register_text_source(
        client,
        source_id="execution-core",
        source_kind="rulebook",
        source_title_zh="执行层核心规则",
        default_priority=35,
    )
    _register_text_source(
        client,
        source_id="execution-house",
        source_kind="house_rule",
        source_title_zh="执行层房规",
        default_priority=85,
    )

    core_ingest = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "execution-core",
            "content": "# 侦查\n侦查用于发现隐藏线索与细微异常。",
        },
    )
    house_ingest = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "execution-house",
            "content": (
                "# 侦查\n"
                "房规：侦查失败时仍可获得核心线索，但会损失少量理智并暴露更多风险。"
            ),
        },
    )
    assert core_ingest.status_code == 200
    assert house_ingest.status_code == 200

    register_sheet = client.post(
        "/knowledge/register-source",
        json={
            "source_id": "execution-sheet",
            "source_kind": "character_sheet",
            "source_format": "xlsx",
            "source_title_zh": "执行测试角色卡",
            "document_identity": "execution-sheet",
            "source_path": str(FIXTURE_DIR / "character_sheet_sample.xlsx"),
            "default_priority": 0,
            "is_authoritative": False,
        },
    )
    assert register_sheet.status_code == 201

    import_sheet = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "execution-sheet"},
    )
    assert import_sheet.status_code == 200
    extraction = import_sheet.json()["extraction"]
    initial_hp = max(
        1,
        (extraction["core_stats"]["constitution"] + extraction["core_stats"]["size"]) // 10,
    )
    initial_san = extraction["core_stats"]["power"]

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "地下室刻痕",
                        "text": "刻痕显示旧印记指向地下祭坛。",
                        "visibility_scope": "kp_only",
                        "core_clue_flag": True,
                        "alternate_paths": ["图书馆使用旧报纸", "失败后依旧可沿墙面刮痕推进"],
                        "fail_forward_text": "即使侦查失败，也会因房规代价找到这条核心线索。",
                    }
                ]
            ),
            "participants": [
                _build_participant_from_extraction(extraction, actor_id="investigator-1"),
                make_participant("investigator-2", "周岚"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    before_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    )
    assert before_review.status_code == 200
    before_payload = before_review.json()
    assert before_payload["current_scene"]["title"] == "开场"
    assert before_payload["own_character_state"]["current_hit_points"] == initial_hp
    assert before_payload["own_character_state"]["current_sanity"] == initial_san
    assert before_payload["own_character_state"]["inventory"] == []
    assert before_payload["scenario"]["clues"] == []

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议按房规推进侦查，并把地下室线索先交给林舟保管。",
            "structured_action": {
                "type": "suggest_action",
                "required_handoff_topic": "term:spot_hidden",
                "scene_transition": {
                    "title": "旅店地下室",
                    "summary": "调查员进入地下室，继续沿着墙面刮痕寻找祭坛。",
                    "phase": "investigation",
                },
                "clue_updates": [
                    {
                        "clue_title": "地下室刻痕",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "discovered_via": "spot_hidden_fail_forward",
                    }
                ],
                "character_updates": [
                    {
                        "actor_id": "investigator-1",
                        "hp_delta": -2,
                        "san_delta": -3,
                        "add_inventory": ["带血钥匙"],
                        "add_status_effects": ["受惊"],
                        "add_temporary_conditions": ["心神不宁"],
                        "add_private_notes": ["我在地下室墙上看见重复符号。"],
                        "add_secret_state_refs": ["sigil-note"],
                    }
                ],
            },
            "rules_query_text": "侦查失败时房规怎么处理",
            "deterministic_resolution_required": True,
        },
    )
    assert draft_response.status_code == 202
    draft_payload = draft_response.json()["draft_action"]
    assert draft_payload["review_status"] == "pending"
    assert draft_payload["rules_grounding"]["deterministic_handoff_topic"] == "term:spot_hidden"

    still_before_review = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    assert still_before_review["current_scene"]["title"] == "开场"
    assert still_before_review["own_character_state"]["current_hit_points"] == initial_hp
    assert still_before_review["own_character_state"]["inventory"] == []
    assert still_before_review["scenario"]["clues"] == []

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_payload['draft_id']}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 200
    reviewed_payload = review_response.json()["reviewed_action"]
    assert reviewed_payload["execution_summary"] is not None
    assert reviewed_payload["applied_state_changes"]
    assert reviewed_payload["rules_grounding"]["citations"]

    investigator_one = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()
    investigator_two = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-2", "viewer_role": "investigator"},
    ).json()
    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()

    assert investigator_one["current_scene"]["title"] == "旅店地下室"
    assert investigator_one["own_character_state"]["current_hit_points"] == initial_hp - 2
    assert investigator_one["own_character_state"]["current_sanity"] == initial_san - 3
    assert "带血钥匙" in investigator_one["own_character_state"]["inventory"]
    assert "受惊" in investigator_one["own_character_state"]["status_effects"]
    assert "心神不宁" in investigator_one["own_character_state"]["temporary_conditions"]
    assert "sigil-note" in investigator_one["own_character_state"]["secret_state_refs"]
    assert any(
        "地下室墙上看见重复符号" in note
        for note in investigator_one["own_character_state"]["private_notes"]
    )

    investigator_one_clues = {
        clue["title"]: clue for clue in investigator_one["scenario"]["clues"]
    }
    assert "地下室刻痕" in investigator_one_clues
    assert investigator_one_clues["地下室刻痕"]["status"] == "private_to_actor"
    assert investigator_one_clues["地下室刻痕"]["discovered_by"] == ["investigator-1"]
    assert investigator_one_clues["地下室刻痕"]["owner_actor_ids"] == ["investigator-1"]
    assert investigator_one_clues["地下室刻痕"]["discovered_via"] == "spot_hidden_fail_forward"
    assert investigator_one_clues["地下室刻痕"]["fail_forward_text"] is not None

    investigator_two_clues = {clue["title"] for clue in investigator_two["scenario"]["clues"]}
    assert "地下室刻痕" not in investigator_two_clues
    assert investigator_two["own_character_state"]["clue_ids"] == []

    assert "带血钥匙" in keeper_state["visible_character_states_by_actor"]["investigator-1"]["inventory"]
    assert "clue-" in keeper_state["visible_character_states_by_actor"]["investigator-1"]["clue_ids"][0]
    reviewed_events = [
        event
        for event in keeper_state["visible_events"]
        if event["event_type"] == "reviewed_action"
    ]
    assert reviewed_events[-1]["structured_payload"]["execution_summary"] is not None
    assert reviewed_events[-1]["structured_payload"]["applied_state_changes"]
    assert reviewed_events[-1]["structured_payload"]["rules_grounding"]["citations"]


def test_execution_layer_rejects_mismatched_deterministic_handoff_topic(
    client: TestClient,
) -> None:
    _register_text_source(
        client,
        source_id="execution-mismatch-core",
        source_kind="rulebook",
        source_title_zh="执行层交接校验规则",
        default_priority=30,
    )
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={
            "source_id": "execution-mismatch-core",
            "content": "# 图书馆使用\n图书馆使用用于在旧报纸与档案中查找资料。",
        },
    )
    assert ingest_response.status_code == 200

    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": make_scenario(
                clues=[
                    {
                        "title": "档案馆索引卡",
                        "text": "索引卡指向失踪案报纸档案。",
                        "visibility_scope": "kp_only",
                    }
                ]
            ),
            "participants": [
                make_participant("investigator-1", "林舟"),
                make_participant("ai-1", "测试调查员", kind="ai"),
            ],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "我建议用档案馆检索推进侦查线索。",
            "structured_action": {
                "type": "suggest_action",
                "required_handoff_topic": "term:spot_hidden",
                "scene_transition": {"title": "档案馆阅览室"},
                "clue_updates": [
                    {
                        "clue_title": "档案馆索引卡",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                    }
                ],
                "character_updates": [
                    {
                        "actor_id": "investigator-1",
                        "add_inventory": ["索引卡复印件"],
                    }
                ],
            },
            "rules_query_text": "查阅旧报纸该用什么技能",
            "deterministic_resolution_required": True,
        },
    )
    assert draft_response.status_code == 202
    draft_id = draft_response.json()["draft_action"]["draft_id"]

    review_response = client.post(
        f"/sessions/{session_id}/draft-actions/{draft_id}/review",
        json={"reviewer_id": "keeper-1", "decision": "approve"},
    )
    assert review_response.status_code == 400
    assert "确定性交接主题" in review_response.json()["detail"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    investigator_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_id": "investigator-1", "viewer_role": "investigator"},
    ).json()

    assert keeper_state["current_scene"]["title"] == "开场"
    assert keeper_state["visible_reviewed_actions"] == []
    assert keeper_state["visible_draft_actions"][0]["review_status"] == "pending"
    assert investigator_state["own_character_state"]["inventory"] == []
    assert investigator_state["scenario"]["clues"] == []
