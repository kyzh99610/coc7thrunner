from __future__ import annotations

import random

from pydantic import BaseModel, Field

from coc_runner.compat import StrEnum


class RollOutcome(StrEnum):
    CRITICAL_SUCCESS = "critical_success"
    EXTREME_SUCCESS = "extreme_success"
    HARD_SUCCESS = "hard_success"
    SUCCESS = "success"
    FAILURE = "failure"
    FUMBLE = "fumble"


class D100Roll(BaseModel):
    seed: int | None = None
    unit_die: int = Field(ge=0, le=9)
    tens_dice: list[int] = Field(default_factory=list)
    selected_tens: int = Field(ge=0, le=9)
    total: int = Field(ge=1, le=100)
    target: int = Field(ge=1, le=100)
    bonus_dice: int = Field(default=0, ge=0)
    penalty_dice: int = Field(default=0, ge=0)
    outcome: RollOutcome


def evaluate_d100_roll(total: int, target: int) -> RollOutcome:
    if not 1 <= total <= 100:
        raise ValueError("d100 total must be between 1 and 100")
    if not 1 <= target <= 100:
        raise ValueError("target must be between 1 and 100")

    if total == 1:
        return RollOutcome.CRITICAL_SUCCESS
    if total == 100 or (total >= 96 and target < 50):
        return RollOutcome.FUMBLE
    if total <= max(1, target // 5):
        return RollOutcome.EXTREME_SUCCESS
    if total <= max(1, target // 2):
        return RollOutcome.HARD_SUCCESS
    if total <= target:
        return RollOutcome.SUCCESS
    return RollOutcome.FAILURE


def roll_d100(
    target: int,
    *,
    seed: int | None = None,
    bonus_dice: int = 0,
    penalty_dice: int = 0,
) -> D100Roll:
    if bonus_dice and penalty_dice:
        raise ValueError("bonus dice and penalty dice cannot both be non-zero")
    if bonus_dice < 0 or penalty_dice < 0:
        raise ValueError("bonus dice and penalty dice must be non-negative")
    if not 1 <= target <= 100:
        raise ValueError("target must be between 1 and 100")

    rng = random.Random(seed)
    unit_die = rng.randint(0, 9)
    extra_tens = bonus_dice or penalty_dice
    tens_dice = [rng.randint(0, 9) for _ in range(extra_tens + 1)]

    if bonus_dice:
        selected_tens = min(tens_dice)
    elif penalty_dice:
        selected_tens = max(tens_dice)
    else:
        selected_tens = tens_dice[0]

    total = selected_tens * 10 + unit_die
    if total == 0:
        total = 100

    return D100Roll(
        seed=seed,
        unit_die=unit_die,
        tens_dice=tens_dice,
        selected_tens=selected_tens,
        total=total,
        target=target,
        bonus_dice=bonus_dice,
        penalty_dice=penalty_dice,
        outcome=evaluate_d100_roll(total, target),
    )
