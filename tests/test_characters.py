from __future__ import annotations

import pytest
from pydantic import ValidationError

from coc_runner.domain.models import Character


def test_character_validation_accepts_valid_investigator() -> None:
    character = Character(
        name="林舟",
        occupation="记者",
        age=30,
        attributes={
            "strength": 45,
            "constitution": 50,
            "size": 55,
            "dexterity": 60,
            "appearance": 40,
            "intelligence": 75,
            "power": 65,
            "education": 70,
        },
        skills={"图书馆使用": 70, "侦查": 60},
    )

    assert character.max_hit_points == 10
    assert character.max_magic_points == 13
    assert character.starting_sanity == 65


def test_character_validation_rejects_out_of_range_skill() -> None:
    with pytest.raises(ValidationError):
        Character(
            name="林舟",
            occupation="记者",
            age=30,
            attributes={
                "strength": 45,
                "constitution": 50,
                "size": 55,
                "dexterity": 60,
                "appearance": 40,
                "intelligence": 75,
                "power": 65,
                "education": 70,
            },
            skills={"图书馆使用": 120},
        )

