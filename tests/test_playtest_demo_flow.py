from __future__ import annotations

from fastapi.testclient import TestClient

from coc_runner.domain.scenario_examples import blackout_clinic_payload
from tests.helpers import make_participant


def test_keeper_demo_summary_stays_coherent_during_short_blackout_clinic_run(
    client: TestClient,
) -> None:
    start_response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "scenario": blackout_clinic_payload(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert start_response.status_code == 201
    session_id = start_response.json()["session_id"]

    intake_action = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我翻看夜班登记簿，确认最后进入病历室的人，并直接推门去病历室。",
            "structured_action": {"type": "review_intake_log"},
            "effects": {
                "scene_transitions": [{"scene_id": "scene.clinic_records"}],
                "clue_state_effects": [
                    {
                        "clue_id": "clue.intake_log",
                        "status": "shared_with_party",
                        "share_with_party": True,
                        "add_discovered_by": ["investigator-1"],
                        "discovered_via": "review_intake_log",
                    }
                ],
            },
        },
    )
    assert intake_action.status_code == 202
    action_id = intake_action.json()["authoritative_action"]["action_id"]

    keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    summary = keeper_state["keeper_workflow"]["summary"]
    active_prompt = keeper_state["keeper_workflow"]["active_prompts"][0]

    assert summary["active_prompt_count"] == 1
    assert summary["unresolved_objective_count"] == 1
    assert any("高优先提示" in line for line in summary["prompt_lines"])
    assert any("待处理场景目标" in line for line in summary["objective_lines"])
    assert any("最近完成目标" in line for line in summary["completed_objective_lines"])
    assert any("最近推进" in line for line in summary["progression_lines"])
    assert "确认停电前最后接触病历室的人是谁" in keeper_state["progress_state"]["completed_objectives"]
    assert keeper_state["progress_state"]["completed_objectives"] == (
        keeper_state["progress_state"]["completed_scene_objectives"]
    )

    prompt_update = client.post(
        f"/sessions/{session_id}/keeper-prompts/{active_prompt['prompt_id']}/status",
        json={
            "operator_id": "keeper-1",
            "status": "acknowledged",
            "add_notes": ["先让护士说完，再决定是否立刻开柜。"],
        },
    )
    assert prompt_update.status_code == 200

    updated_keeper_state = client.get(
        f"/sessions/{session_id}/state",
        params={"viewer_role": "keeper"},
    ).json()
    updated_summary = updated_keeper_state["keeper_workflow"]["summary"]
    assert any("已确认" in line for line in updated_summary["prompt_lines"])
    assert any("备注：先让护士说完，再决定是否立刻开柜。" in line for line in updated_summary["prompt_lines"])
    assert any(action_id in line for line in updated_summary["progression_lines"])
    assert any("原因：" in line for line in updated_summary["summary_lines"])
