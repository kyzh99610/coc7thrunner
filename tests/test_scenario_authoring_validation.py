from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from coc_runner.domain.models import ScenarioScaffold
from coc_runner.domain.scenario_examples import (
    blackout_clinic_payload,
    midnight_archive_payload,
    whispering_guesthouse_payload,
)
from tests.helpers import make_scenario


def _base_scenario_payload() -> dict[str, Any]:
    return make_scenario(
        start_scene_id="scene.study",
        clues=[
            {
                "clue_id": "clue.ledger",
                "title": "账页残片",
                "text": "残片记录着一个可疑房间号。",
                "visibility_scope": "kp_only",
            }
        ],
        scenes=[
            {
                "scene_id": "scene.study",
                "title": "书房",
                "summary": "书桌上堆着账册和湿掉的纸张。",
                "revealed": True,
                "linked_clue_ids": ["clue.ledger"],
                "scene_objectives": [
                    {
                        "objective_id": "objective.study.review_ledger",
                        "text": "确认账页是否能推动下一步调查",
                        "beat_id": "beat.review_ledger",
                    }
                ],
            }
        ],
        beats=[
            {
                "beat_id": "beat.review_ledger",
                "title": "查看账页",
                "start_unlocked": True,
                "complete_conditions": {
                    "clue_discovered": {"clue_id": "clue.ledger"}
                },
            }
        ],
    )


def _assert_validation_error(payload: dict[str, Any], *fragments: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ScenarioScaffold.model_validate(payload)
    message = str(exc.value)
    for fragment in fragments:
        assert fragment in message


def test_authored_scenario_examples_validate_cleanly() -> None:
    for payload in (
        whispering_guesthouse_payload(),
        midnight_archive_payload(),
        blackout_clinic_payload(),
    ):
        scenario = ScenarioScaffold.model_validate(payload)
        assert scenario.start_scene_id is None or any(
            scene.scene_id == scenario.start_scene_id for scene in scenario.scenes
        )


def test_scenario_validation_rejects_unknown_start_scene_id_early() -> None:
    payload = _base_scenario_payload()
    payload["start_scene_id"] = "scene.missing"

    _assert_validation_error(
        payload,
        "scenario start_scene_id scene.missing was not found",
    )


@pytest.mark.parametrize(
    ("mutator", "expected_fragments"),
    [
        pytest.param(
            lambda payload: payload["scenes"].append(
                {
                    "scene_id": "scene.study",
                    "title": "重复书房",
                    "summary": "这个 scene_id 不应重复。",
                }
            ),
            ("scenario scene_id scene.study must be unique",),
            id="duplicate-scene-id",
        ),
        pytest.param(
            lambda payload: payload["clues"].append(
                {
                    "clue_id": "clue.ledger",
                    "title": "重复账页",
                    "text": "这个 clue_id 不应重复。",
                    "visibility_scope": "kp_only",
                }
            ),
            ("scenario clue_id clue.ledger must be unique",),
            id="duplicate-clue-id",
        ),
        pytest.param(
            lambda payload: payload["beats"].append(
                {
                    "beat_id": "beat.review_ledger",
                    "title": "重复节点",
                }
            ),
            ("scenario beat_id beat.review_ledger must be unique",),
            id="duplicate-beat-id",
        ),
    ],
)
def test_scenario_validation_rejects_duplicate_ids_with_offending_id(
    mutator,
    expected_fragments: tuple[str, ...],
) -> None:
    payload = _base_scenario_payload()
    mutator(payload)

    _assert_validation_error(payload, *expected_fragments)


@pytest.mark.parametrize(
    ("mutator", "expected_fragments"),
    [
        pytest.param(
            lambda payload: payload["scenes"][0]["scene_objectives"][0].update(
                {"beat_id": "beat.missing"}
            ),
            (
                "scenario scene objective objective.study.review_ledger references unknown beat beat.missing",
            ),
            id="scene-objective-unknown-beat",
        ),
        pytest.param(
            lambda payload: payload["beats"][0]["complete_conditions"]["clue_discovered"].update(
                {"clue_id": "clue.missing"}
            ),
            (
                "scenario beat beat.review_ledger condition references unknown clue clue.missing",
            ),
            id="condition-unknown-clue",
        ),
        pytest.param(
            lambda payload: payload["beats"][0].update({"next_beats": ["beat.followup_missing"]}),
            (
                "scenario beat beat.review_ledger references unknown next beat beat.followup_missing",
            ),
            id="next-beat-unknown",
        ),
    ],
)
def test_scenario_validation_rejects_invalid_scene_objective_and_beat_refs(
    mutator,
    expected_fragments: tuple[str, ...],
) -> None:
    payload = _base_scenario_payload()
    mutator(payload)

    _assert_validation_error(payload, *expected_fragments)


@pytest.mark.parametrize(
    ("mutator", "expected_fragments"),
    [
        pytest.param(
            lambda payload: payload["beats"][0].update(
                {
                    "consequences": [
                        {
                            "reveal_scenes": [{"scene_id": "scene.hidden_basement"}],
                        }
                    ]
                }
            ),
            (
                "scenario beat beat.review_ledger consequence references unknown scene scene.hidden_basement",
            ),
            id="reveal-scene-unknown",
        ),
        pytest.param(
            lambda payload: payload["beats"][0].update(
                {
                    "consequences": [
                        {
                            "reveal_clues": [{"clue_id": "clue.hidden_note"}],
                        }
                    ]
                }
            ),
            (
                "scenario beat beat.review_ledger consequence references unknown clue clue.hidden_note",
            ),
            id="reveal-clue-unknown",
        ),
        pytest.param(
            lambda payload: payload["beats"][0].update(
                {
                    "consequences": [
                        {
                            "mark_scene_objectives_complete": [
                                {"objective_id": "objective.missing"}
                            ]
                        }
                    ]
                }
            ),
            (
                "scenario beat beat.review_ledger consequence references unknown objective objective.missing",
            ),
            id="mark-objective-unknown",
        ),
    ],
)
def test_scenario_validation_rejects_invalid_consequence_targets(
    mutator,
    expected_fragments: tuple[str, ...],
) -> None:
    payload = _base_scenario_payload()
    mutator(payload)

    _assert_validation_error(payload, *expected_fragments)


def test_scenario_validation_rejects_beat_graph_without_entry_beat() -> None:
    payload = _base_scenario_payload()
    payload["beats"][0]["start_unlocked"] = False

    _assert_validation_error(
        payload,
        "scenario beat graph has no entry beat",
        "start_unlocked",
    )


def test_scenario_validation_rejects_unreachable_orphan_beat() -> None:
    payload = _base_scenario_payload()
    payload["beats"].append(
        {
            "beat_id": "beat.orphan",
            "title": "孤立节点",
        }
    )

    _assert_validation_error(
        payload,
        "scenario beat graph has unreachable beat(s): beat.orphan",
    )


@pytest.mark.parametrize(
    ("mutator", "expected_fragments"),
    [
        pytest.param(
            lambda payload: payload["beats"][0].update({"next_beats": ["beat.review_ledger"]}),
            ("scenario beat beat.review_ledger cannot reference itself via next_beats",),
            id="self-loop-next-beat",
        ),
        pytest.param(
            lambda payload: payload["beats"][0].update(
                {"consequences": [{"unlock_beat_ids": ["beat.review_ledger"]}]}
            ),
            ("scenario beat beat.review_ledger consequence cannot unlock itself",),
            id="self-loop-consequence-unlock",
        ),
    ],
)
def test_scenario_validation_rejects_direct_self_loops_in_beat_flow(
    mutator,
    expected_fragments: tuple[str, ...],
) -> None:
    payload = _base_scenario_payload()
    mutator(payload)

    _assert_validation_error(payload, *expected_fragments)


def test_scenario_validation_rejects_simple_cycle_in_beat_flow_graph() -> None:
    payload = _base_scenario_payload()
    payload["beats"] = [
        {
            "beat_id": "beat.alpha",
            "title": "起始节点",
            "start_unlocked": True,
            "complete_conditions": {
                "clue_discovered": {"clue_id": "clue.ledger"}
            },
            "next_beats": ["beat.beta"],
        },
        {
            "beat_id": "beat.beta",
            "title": "回环节点",
            "complete_conditions": {
                "clue_discovered": {"clue_id": "clue.ledger"}
            },
            "next_beats": ["beat.alpha"],
        },
    ]
    payload["scenes"][0]["scene_objectives"][0]["beat_id"] = "beat.alpha"

    _assert_validation_error(
        payload,
        "scenario beat graph contains cycle",
        "beat.alpha",
        "beat.beta",
    )


def test_scenario_validation_allows_entry_beat_without_start_unlocked_when_no_beat_dependency() -> None:
    payload = _base_scenario_payload()
    payload["beats"] = [
        {
            "beat_id": "beat.entry_from_scene",
            "title": "场景直接入口节点",
            "unlock_conditions": {
                "scene_is": {"scene_id": "scene.study"}
            },
            "complete_conditions": {
                "clue_discovered": {"clue_id": "clue.ledger"}
            },
        },
        {
            "beat_id": "beat.followup",
            "title": "后续节点",
            "unlock_conditions": {
                "beat_status_is": {
                    "beat_id": "beat.entry_from_scene",
                    "status": "completed",
                }
            },
        },
    ]
    payload["scenes"][0]["scene_objectives"][0]["beat_id"] = "beat.entry_from_scene"

    scenario = ScenarioScaffold.model_validate(payload)
    assert [beat.beat_id for beat in scenario.beats] == [
        "beat.entry_from_scene",
        "beat.followup",
    ]


def test_scenario_validation_rejects_non_terminal_beat_without_completion_path() -> None:
    payload = _base_scenario_payload()
    payload["beats"][0].pop("complete_conditions")
    payload["beats"][0]["next_beats"] = ["beat.followup"]
    payload["beats"].append(
        {
            "beat_id": "beat.followup",
            "title": "后续节点",
        }
    )

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger can never complete",
        "beat.followup",
    )


def test_scenario_validation_rejects_complete_conditions_with_conflicting_beat_statuses() -> None:
    payload = _base_scenario_payload()
    payload["beats"] = [
        {
            "beat_id": "beat.alpha",
            "title": "前置节点",
            "start_unlocked": True,
            "complete_conditions": {
                "clue_discovered": {"clue_id": "clue.ledger"}
            },
        },
        {
            "beat_id": "beat.beta",
            "title": "矛盾完成节点",
            "start_unlocked": True,
            "complete_conditions": {
                "all_of": [
                    {
                        "beat_status_is": {
                            "beat_id": "beat.alpha",
                            "status": "current",
                        }
                    },
                    {
                        "beat_status_is": {
                            "beat_id": "beat.alpha",
                            "status": "completed",
                        }
                    },
                ]
            },
        },
    ]
    payload["scenes"][0]["scene_objectives"][0]["beat_id"] = "beat.alpha"

    _assert_validation_error(
        payload,
        "scenario beat beat.beta complete_conditions can never be satisfied",
        "beat.alpha",
        "current",
        "completed",
    )


def test_scenario_validation_rejects_complete_conditions_with_conflicting_scene_requirements() -> None:
    payload = _base_scenario_payload()
    payload["scenes"].append(
        {
            "scene_id": "scene.cellar",
            "title": "地窖",
            "summary": "这里和书房不是同一个 scene。",
        }
    )
    payload["beats"][0]["complete_conditions"] = {
        "all_of": [
            {"scene_is": {"scene_id": "scene.study"}},
            {"current_scene_in": {"scene_ids": ["scene.cellar"]}},
        ]
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger complete_conditions can never be satisfied",
        "scene.study",
        "scene.cellar",
    )


def test_scenario_validation_rejects_complete_conditions_requiring_own_completed_status() -> None:
    payload = _base_scenario_payload()
    payload["beats"][0]["complete_conditions"] = {
        "beat_status_is": {
            "beat_id": "beat.review_ledger",
            "status": "completed",
        }
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger complete_conditions can never be satisfied",
        "beat.review_ledger",
        "completed",
    )


def test_scenario_validation_rejects_block_conditions_that_shadow_completion() -> None:
    payload = _base_scenario_payload()
    payload["beats"][0]["block_conditions"] = {
        "clue_discovered": {"clue_id": "clue.ledger"}
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger block_conditions contradict complete_conditions",
    )


def test_scenario_validation_allows_completion_any_of_with_one_impossible_branch() -> None:
    payload = _base_scenario_payload()
    payload["scenes"].append(
        {
            "scene_id": "scene.cellar",
            "title": "地窖",
            "summary": "这里只用于构造一条不可能分支。",
        }
    )
    payload["beats"][0]["complete_conditions"] = {
        "any_of": [
            {
                "all_of": [
                    {"scene_is": {"scene_id": "scene.study"}},
                    {"current_scene_in": {"scene_ids": ["scene.cellar"]}},
                ]
            },
            {"clue_discovered": {"clue_id": "clue.ledger"}},
        ]
    }

    scenario = ScenarioScaffold.model_validate(payload)
    assert scenario.beats[0].beat_id == "beat.review_ledger"
    assert len(scenario.beats[0].complete_conditions.any_of) == 2


def test_scenario_validation_rejects_unlock_conditions_with_conflicting_scene_title_requirements() -> None:
    payload = _base_scenario_payload()
    payload["scenes"].append(
        {
            "scene_id": "scene.cellar",
            "title": "地窖",
            "summary": "这里不是书房。",
        }
    )
    payload["beats"][0]["start_unlocked"] = False
    payload["beats"][0]["unlock_conditions"] = {
        "all_of": [
            {"scene_is": {"title": "书房"}},
            {"current_scene_in": {"titles": ["地窖"]}},
        ]
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger unlock_conditions can never be satisfied",
        "书房",
        "地窖",
    )


def test_scenario_validation_rejects_complete_conditions_with_conflicting_scene_phase_requirements() -> None:
    payload = _base_scenario_payload()
    payload["scenes"].append(
        {
            "scene_id": "scene.ritual_room",
            "title": "祭坛间",
            "summary": "这里只用于制造 phase 冲突。",
            "phase": "ritual",
        }
    )
    payload["beats"][0]["complete_conditions"] = {
        "all_of": [
            {"scene_is": {"phase": "investigation"}},
            {"current_scene_in": {"phases": ["ritual"]}},
        ]
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger complete_conditions can never be satisfied",
        "investigation",
        "ritual",
    )


def test_scenario_validation_rejects_current_scene_in_titles_with_unknown_scene_title() -> None:
    payload = _base_scenario_payload()
    payload["beats"][0]["unlock_conditions"] = {
        "current_scene_in": {"titles": ["不存在的阁楼"]}
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger condition references unknown scene title 不存在的阁楼",
    )


def test_scenario_validation_rejects_current_scene_in_phases_with_unknown_scene_phase() -> None:
    payload = _base_scenario_payload()
    payload["beats"][0]["complete_conditions"] = {
        "current_scene_in": {"phases": ["combat"]}
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger condition references unknown scene phase combat",
    )


def test_scenario_validation_rejects_unlock_conditions_with_impossible_scene_title_phase_combo() -> None:
    payload = _base_scenario_payload()
    payload["scenes"].append(
        {
            "scene_id": "scene.dream_attic",
            "title": "阁楼",
            "summary": "这里只用于制造 title 和 phase 的显式无解组合。",
            "phase": "dream",
        }
    )
    payload["beats"][0]["start_unlocked"] = False
    payload["beats"][0]["unlock_conditions"] = {
        "scene_is": {"title": "书房", "phase": "dream"}
    }

    _assert_validation_error(
        payload,
        "scenario beat beat.review_ledger unlock_conditions can never be satisfied",
        "书房",
        "dream",
    )


def test_scenario_validation_allows_scene_title_phase_condition_with_viable_branch() -> None:
    payload = _base_scenario_payload()
    payload["scenes"].append(
        {
            "scene_id": "scene.cellar",
            "title": "地窖",
            "summary": "这里只用于提供第二个可选场景。",
            "phase": "ritual",
        }
    )
    payload["beats"][0]["complete_conditions"] = {
        "any_of": [
            {
                "all_of": [
                    {"scene_is": {"title": "书房"}},
                    {"current_scene_in": {"phases": ["ritual"]}},
                ]
            },
            {
                "all_of": [
                    {"scene_is": {"title": "书房"}},
                    {
                        "current_scene_in": {
                            "titles": ["书房", "地窖"],
                            "phases": ["investigation", "ritual"],
                        }
                    },
                    {"clue_discovered": {"clue_id": "clue.ledger"}},
                ]
            },
        ]
    }

    scenario = ScenarioScaffold.model_validate(payload)
    assert scenario.beats[0].beat_id == "beat.review_ledger"
    assert len(scenario.beats[0].complete_conditions.any_of) == 2
