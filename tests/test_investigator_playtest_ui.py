from __future__ import annotations

import shutil

import coc_runner.application.session_service as session_service_module
from fastapi.testclient import TestClient
from coc_runner.domain.dice import D100Roll, RollOutcome

from tests.helpers import make_participant
from tests.test_session_import import (
    KEEPER_ID,
    _discover_note_and_queue_prompt,
    _get_snapshot,
    _import_character_sheet_source,
    _import_snapshot,
    _make_cross_environment_client,
    _snapshot_scenario,
)


def _start_investigator_ui_session(
    client: TestClient,
    *,
    participants: list[dict] | None = None,
    scenario: dict | None = None,
) -> str:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": "keeper-1",
            "scenario": scenario or _snapshot_scenario(),
            "participants": participants or [make_participant("investigator-1", "林舟")],
        },
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def test_investigator_playtest_page_opens_with_summary_and_action_form(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(client)

    response = client.get(f"/playtest/sessions/{session_id}/investigator/investigator-1")

    assert response.status_code == 200
    html = response.text
    assert "林舟 的调查页面" in html
    assert session_id in html
    assert "viewer_id: <code>investigator-1</code>" in html
    assert "当前场景：旅店前厅" in html
    assert "提交玩家行动" in html
    assert 'name="action_text"' in html
    assert "提交行动" in html
    assert "快速技能检定" in html
    assert 'name="skill_name"' in html
    assert 'value="图书馆使用"' in html
    assert 'value="侦查"' in html
    assert "开始检定" in html
    assert "快速属性检定" in html
    assert 'name="attribute_name"' in html
    assert 'value="strength"' in html
    assert 'value="education"' in html
    assert "开始属性检定" in html
    assert "快速理智检定" in html
    assert 'name="source_label"' in html
    assert 'name="success_loss"' in html
    assert 'name="failure_loss"' in html
    assert "开始理智检定" in html
    assert "本局已结束" not in html
    assert f'/playtest/sessions/{session_id}/home"' in html
    assert "最近可见事件" in html
    assert "会话已创建：迷雾中的旅店" in html
    assert "职业：记者" in html
    assert "年龄：28" in html
    assert "力量 50" in html
    assert "图书馆使用 70" in html
    assert "随身物品" in html
    assert "当前没有可见的随身物品。" in html
    assert "私有备注与记录" in html
    assert "林舟 的私人笔记" in html
    assert "KP 提示" not in html
    assert "visible_reviewed_actions" not in html
    assert "keeper_workflow" not in html


def test_investigator_playtest_page_shows_completed_notice_and_hides_action_form_when_session_completed(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(client)

    activate_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "active"},
    )
    assert activate_response.status_code == 200

    complete_response = client.post(
        f"/playtest/sessions/{session_id}/keeper/lifecycle",
        data={"operator_id": KEEPER_ID, "target_status": "completed"},
    )
    assert complete_response.status_code == 200

    response = client.get(f"/playtest/sessions/{session_id}/investigator/investigator-1")

    assert response.status_code == 200
    html = response.text
    assert "本局已结束" in html
    assert "当前页面保留结束后的查看状态" in html
    assert "当前场景：旅店前厅" in html
    assert "最近可见事件" in html
    assert "会话已创建：迷雾中的旅店" in html
    assert "提交玩家行动" in html
    assert "本局已结束，当前页面不再提交新的玩家行动。" in html
    assert "快速技能检定" in html
    assert "本局已结束，当前页面不再进行新的技能检定。" in html
    assert "快速属性检定" in html
    assert "本局已结束，当前页面不再进行新的属性检定。" in html
    assert "快速理智检定" in html
    assert "本局已结束，当前页面不再进行新的理智检定。" in html
    assert "职业：记者" in html
    assert "图书馆使用 70" in html
    assert "私有备注与记录" in html
    assert "林舟 的私人笔记" in html
    assert 'name="action_text"' not in html
    assert 'name="skill_name"' not in html
    assert 'name="attribute_name"' not in html
    assert 'name="source_label"' not in html
    assert 'name="success_loss"' not in html
    assert 'name="failure_loss"' not in html
    assert "提交行动" not in html
    assert "开始检定" not in html
    assert "开始属性检定" not in html
    assert "开始理智检定" not in html


def test_investigator_playtest_page_preserves_private_visibility_without_keeper_leakage(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(
        client,
        participants=[
            make_participant("investigator-1", "林舟"),
            make_participant("investigator-2", "周岚"),
            make_participant("ai-1", "测试调查员", kind="ai"),
        ],
    )
    _discover_note_and_queue_prompt(client, session_id)
    private_reveal = client.post(
        f"/sessions/{session_id}/manual-action",
        json={
            "operator_id": "keeper-1",
            "actor_id": "investigator-1",
            "actor_type": "investigator",
            "action_text": "KP 确认林舟独自收起一件只对自己可见的发现。",
            "structured_action": {"type": "confirm_private_discovery"},
            "effects": {
                "clue_state_effects": [
                    {
                        "clue_id": "clue-private-journal",
                        "status": "private_to_actor",
                        "private_to_actor_ids": ["investigator-1"],
                        "add_owner_actor_ids": ["investigator-1"],
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "confirm_private_discovery",
                    }
                ],
                "status_effects": [
                    {
                        "actor_id": "investigator-1",
                        "add_private_notes": ["只有林舟能看到的补充备注。"],
                    }
                ],
            },
        },
    )
    assert private_reveal.status_code == 202

    draft_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "ai-1",
            "action_text": "AI 草稿不应泄露给调查员。",
            "structured_action": {"type": "ai_hidden_draft"},
            "risk_level": "high",
            "requires_explicit_approval": True,
        },
    )
    assert draft_response.status_code == 202

    investigator_one = client.get(
        f"/playtest/sessions/{session_id}/investigator/investigator-1"
    )
    investigator_two = client.get(
        f"/playtest/sessions/{session_id}/investigator/investigator-2"
    )

    assert investigator_one.status_code == 200
    assert investigator_two.status_code == 200
    html_one = investigator_one.text
    html_two = investigator_two.text

    assert "残缺日记" in html_one
    assert "残缺日记" not in html_two
    assert "日记只对真正翻到它的人暴露房内真相。" in html_one
    assert "日记只对真正翻到它的人暴露房内真相。" not in html_two
    assert "状态：仅自己可见" in html_one
    assert "只有林舟能看到的补充备注。" in html_one
    assert "只有林舟能看到的补充备注。" not in html_two
    assert "会话备注" in html_one
    assert "会话备注" not in html_two
    assert "KP：档案室的低语压力已具备条件，请保留一次理智审阅。" not in html_one
    assert "AI 草稿不应泄露给调查员。" not in html_one
    assert "visible_reviewed_actions" not in html_one
    assert "keeper_workflow" not in html_one


def test_investigator_playtest_page_shows_recent_visible_events_newest_first(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(client)

    first = client.post(
        f"/sessions/{session_id}/player-action",
        json={"actor_id": "investigator-1", "action_text": "我先检查前厅的门锁。"},
    )
    second = client.post(
        f"/sessions/{session_id}/player-action",
        json={"actor_id": "investigator-1", "action_text": "我再查看柜台后的潮湿痕迹。"},
    )
    assert first.status_code == 202
    assert second.status_code == 202

    response = client.get(f"/playtest/sessions/{session_id}/investigator/investigator-1")

    assert response.status_code == 200
    html = response.text
    assert "我再查看柜台后的潮湿痕迹。" in html
    assert "我先检查前厅的门锁。" in html
    assert html.index("我再查看柜台后的潮湿痕迹。") < html.index("我先检查前厅的门锁。")


def test_investigator_playtest_page_player_action_form_submission_rerenders_with_result(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(client)

    response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/actions",
        data={"action_text": "我检查前厅地板上的水痕。"},
    )

    assert response.status_code == 200
    html = response.text
    assert "最近一次提交结果" in html
    assert "已记录玩家行动" in html
    assert "我检查前厅地板上的水痕。" in html
    assert "最近可见事件" in html


def test_investigator_playtest_page_skill_check_submission_rerenders_with_result(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_investigator_ui_session(client)

    def _fixed_roll(target: int, *, seed: int | None = None, bonus_dice: int = 0, penalty_dice: int = 0) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=5,
            tens_dice=[3],
            selected_tens=3,
            total=35,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.SUCCESS,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)

    response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/skill-check",
        data={"skill_name": "图书馆使用"},
    )

    assert response.status_code == 200
    html = response.text
    assert "最近一次检定结果" in html
    assert "已完成技能检定" in html
    assert "类型：技能检定" in html
    assert "项目：图书馆使用" in html
    assert "数值：70" in html
    assert "掷骰结果：35" in html
    assert "判定：成功" in html
    assert html.index("类型：技能检定") < html.index("项目：图书馆使用")
    assert html.index("项目：图书馆使用") < html.index("数值：70")
    assert html.index("数值：70") < html.index("掷骰结果：35")
    assert html.index("掷骰结果：35") < html.index("判定：成功")


def test_investigator_playtest_page_attribute_check_submission_rerenders_with_result(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_investigator_ui_session(client)

    def _fixed_roll(target: int, *, seed: int | None = None, bonus_dice: int = 0, penalty_dice: int = 0) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=2,
            tens_dice=[2],
            selected_tens=2,
            total=22,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.HARD_SUCCESS,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)

    response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/attribute-check",
        data={"attribute_name": "education"},
    )

    assert response.status_code == 200
    html = response.text
    assert "最近一次检定结果" in html
    assert "已完成属性检定" in html
    assert "类型：属性检定" in html
    assert "项目：教育" in html
    assert "数值：75" in html
    assert "掷骰结果：22" in html
    assert "判定：困难成功" in html
    assert html.index("类型：属性检定") < html.index("项目：教育")
    assert html.index("项目：教育") < html.index("数值：75")
    assert html.index("数值：75") < html.index("掷骰结果：22")
    assert html.index("掷骰结果：22") < html.index("判定：困难成功")


def test_investigator_playtest_page_san_check_submission_rerenders_with_persisted_san_result(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_investigator_ui_session(client)

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", lambda expression: 3)

    before_snapshot = _get_snapshot(client, session_id)
    before_sanity = before_snapshot["character_states"]["investigator-1"]["current_sanity"]

    response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄衣之王的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "最近一次检定结果" in html
    assert "已完成理智检定，当前 SAN 已更新" in html
    assert "类型：理智检定" in html
    assert "项目：黄衣之王的近距离显现" in html
    assert "检定前 SAN：60" in html
    assert "掷骰结果：88" in html
    assert "判定：失败" in html
    assert "成功损失：1" in html
    assert "失败损失：1d6" in html
    assert "本次 SAN 损失：3（依据 1d6）" in html
    assert "检定后 SAN：57" in html
    assert "SAN：57" in html

    after_snapshot = _get_snapshot(client, session_id)
    assert before_sanity == 60
    assert after_snapshot["character_states"]["investigator-1"]["current_sanity"] == 57


def test_investigator_san_check_zero_loss_keeps_san_and_large_loss_clamps_to_zero(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_investigator_ui_session(client)

    def _fixed_failure_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=8,
            tens_dice=[8],
            selected_tens=8,
            total=88,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_failure_roll)
    monkeypatch.setattr(
        session_service_module,
        "_roll_san_loss_value",
        lambda expression: int(expression),
    )

    zero_loss_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "远处传来的低语",
            "success_loss": "0",
            "failure_loss": "0",
        },
    )

    assert zero_loss_response.status_code == 200
    zero_loss_html = zero_loss_response.text
    assert "项目：远处传来的低语" in zero_loss_html
    assert "本次 SAN 损失：0（依据 0）" in zero_loss_html
    assert "检定前 SAN：60" in zero_loss_html
    assert "检定后 SAN：60" in zero_loss_html
    assert "SAN：60" in zero_loss_html
    zero_loss_snapshot = _get_snapshot(client, session_id)
    assert zero_loss_snapshot["character_states"]["investigator-1"]["current_sanity"] == 60

    clamp_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "黄印撕开帷幕的瞬间",
            "success_loss": "0",
            "failure_loss": "99",
        },
    )

    assert clamp_response.status_code == 200
    clamp_html = clamp_response.text
    assert "项目：黄印撕开帷幕的瞬间" in clamp_html
    assert "本次 SAN 损失：99（依据 99）" in clamp_html
    assert "检定前 SAN：60" in clamp_html
    assert "检定后 SAN：0" in clamp_html
    assert "SAN：0" in clamp_html
    clamped_snapshot = _get_snapshot(client, session_id)
    assert clamped_snapshot["character_states"]["investigator-1"]["current_sanity"] == 0


def test_investigator_san_check_supports_contextual_loss_parameters_without_fixed_monster_mapping(
    client: TestClient,
    monkeypatch,
) -> None:
    session_id = _start_investigator_ui_session(client)

    def _fixed_roll(
        target: int,
        *,
        seed: int | None = None,
        bonus_dice: int = 0,
        penalty_dice: int = 0,
    ) -> D100Roll:
        return D100Roll(
            seed=seed,
            unit_die=7,
            tens_dice=[7],
            selected_tens=7,
            total=77,
            target=target,
            bonus_dice=bonus_dice,
            penalty_dice=penalty_dice,
            outcome=RollOutcome.FAILURE,
        )

    def _fixed_san_loss(expression: str) -> int:
        return {
            "1d3": 2,
            "1d6": 5,
            "0": 0,
            "1": 1,
        }[expression]

    monkeypatch.setattr(session_service_module, "roll_d100", _fixed_roll)
    monkeypatch.setattr(session_service_module, "_roll_san_loss_value", _fixed_san_loss)

    subtle_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "哈斯塔的模糊倒影",
            "success_loss": "0",
            "failure_loss": "1d3",
        },
    )
    direct_response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/san-check",
        data={
            "source_label": "哈斯塔的近距离显现",
            "success_loss": "1",
            "failure_loss": "1d6",
        },
    )

    assert subtle_response.status_code == 200
    subtle_html = subtle_response.text
    assert "项目：哈斯塔的模糊倒影" in subtle_html
    assert "成功损失：0" in subtle_html
    assert "失败损失：1d3" in subtle_html
    assert "本次 SAN 损失：2（依据 1d3）" in subtle_html
    assert "检定前 SAN：60" in subtle_html
    assert "检定后 SAN：58" in subtle_html

    assert direct_response.status_code == 200
    direct_html = direct_response.text
    assert "项目：哈斯塔的近距离显现" in direct_html
    assert "成功损失：1" in direct_html
    assert "失败损失：1d6" in direct_html
    assert "本次 SAN 损失：5（依据 1d6）" in direct_html
    assert "检定前 SAN：58" in direct_html
    assert "检定后 SAN：53" in direct_html

    after_snapshot = _get_snapshot(client, session_id)
    assert after_snapshot["character_states"]["investigator-1"]["current_sanity"] == 53


def test_investigator_playtest_page_invalid_action_shows_structured_error(
    client: TestClient,
) -> None:
    session_id = _start_investigator_ui_session(client)

    response = client.post(
        f"/playtest/sessions/{session_id}/investigator/investigator-1/actions",
        data={"action_text": ""},
    )

    assert response.status_code == 422
    html = response.text
    assert "操作失败" in html
    assert "请求参数校验失败" in html
    assert "request_validation_failed" in html


def test_investigator_playtest_page_surfaces_grounding_degraded_without_keeper_review_metadata() -> None:
    character_source_id = "character-sheet-template-investigator-ui-grounding"
    source_client, source_run_dir = _make_cross_environment_client("investigator_ui_source")
    target_client, target_run_dir = _make_cross_environment_client("investigator_ui_target")
    try:
        with source_client, target_client:
            _import_character_sheet_source(source_client, source_id=character_source_id)
            start_response = source_client.post(
                "/sessions/start",
                json={
                    "keeper_name": "KP",
                    "scenario": _snapshot_scenario(),
                    "participants": [
                        make_participant(
                            "investigator-1",
                            "林舟",
                            imported_character_source_id=character_source_id,
                        )
                    ],
                },
            )
            assert start_response.status_code == 201
            source_session_id = start_response.json()["session_id"]
            snapshot = _get_snapshot(source_client, source_session_id)
            imported_payload = _import_snapshot(target_client, snapshot)
            imported_session_id = imported_payload["new_session_id"]

            response = target_client.post(
                f"/playtest/sessions/{imported_session_id}/investigator/investigator-1/actions",
                data={"action_text": "我去图书馆查阅旧报纸。"},
            )

            assert response.status_code == 200
            html = response.text
            assert "规则依据降级" in html
            assert "当前环境缺少外部知识源" in html
            assert "visible_reviewed_actions" not in html
            assert "review_id" not in html
    finally:
        shutil.rmtree(source_run_dir, ignore_errors=True)
        shutil.rmtree(target_run_dir, ignore_errors=True)


def test_investigator_playtest_page_missing_session_gracefully_renders_error_page(
    client: TestClient,
) -> None:
    response = client.get("/playtest/sessions/session-missing/investigator/investigator-1")

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "未找到会话 session-missing" in html
    assert "session_state_session_not_found" in html
