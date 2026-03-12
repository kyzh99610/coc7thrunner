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
