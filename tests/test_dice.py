from __future__ import annotations

from coc_runner.domain.dice import RollOutcome, evaluate_d100_roll, roll_d100


def test_d100_roll_is_reproducible_with_seed() -> None:
    first = roll_d100(target=60, seed=42)
    second = roll_d100(target=60, seed=42)

    assert first.total == second.total
    assert first.unit_die == second.unit_die
    assert first.tens_dice == second.tens_dice
    assert first.outcome == second.outcome


def test_d100_roll_classification_matches_thresholds() -> None:
    assert evaluate_d100_roll(total=1, target=60) == RollOutcome.CRITICAL_SUCCESS
    assert evaluate_d100_roll(total=12, target=60) == RollOutcome.EXTREME_SUCCESS
    assert evaluate_d100_roll(total=30, target=60) == RollOutcome.HARD_SUCCESS
    assert evaluate_d100_roll(total=55, target=60) == RollOutcome.SUCCESS
    assert evaluate_d100_roll(total=88, target=60) == RollOutcome.FAILURE
    assert evaluate_d100_roll(total=100, target=60) == RollOutcome.FUMBLE

