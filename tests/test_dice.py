from __future__ import annotations

from coc_runner.domain.dice import (
    AttackDefenseMode,
    AttackResolution,
    D100Roll,
    HitLocation,
    RollOutcome,
    compute_damage_bonus_expression,
    evaluate_d100_roll,
    evaluate_heavy_wound,
    evaluate_melee_attack_resolution,
    evaluate_ranged_attack_resolution,
    resolve_hit_location,
    roll_d100,
    roll_damage_expression,
)


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


def test_attack_resolution_helpers_keep_authoritative_hit_and_defense_logic_local() -> None:
    actor_roll = D100Roll(
        unit_die=3,
        tens_dice=[2],
        selected_tens=2,
        total=23,
        target=55,
        outcome=RollOutcome.HARD_SUCCESS,
    )
    defender_roll = D100Roll(
        unit_die=2,
        tens_dice=[6],
        selected_tens=6,
        total=62,
        target=40,
        outcome=RollOutcome.FAILURE,
    )
    counterattack_roll = D100Roll(
        unit_die=8,
        tens_dice=[1],
        selected_tens=1,
        total=18,
        target=50,
        outcome=RollOutcome.HARD_SUCCESS,
    )
    failed_attack_roll = D100Roll(
        unit_die=3,
        tens_dice=[7],
        selected_tens=7,
        total=73,
        target=55,
        outcome=RollOutcome.FAILURE,
    )
    draw_roll = D100Roll(
        unit_die=2,
        tens_dice=[4],
        selected_tens=4,
        total=42,
        target=60,
        outcome=RollOutcome.SUCCESS,
    )
    double_failure_roll = D100Roll(
        unit_die=3,
        tens_dice=[8],
        selected_tens=8,
        total=83,
        target=55,
        outcome=RollOutcome.FAILURE,
    )

    assert evaluate_melee_attack_resolution(
        actor_roll=actor_roll,
        defender_roll=defender_roll,
        defense_mode=AttackDefenseMode.DODGE,
    ) == AttackResolution.HIT
    assert evaluate_melee_attack_resolution(
        actor_roll=failed_attack_roll,
        defender_roll=counterattack_roll,
        defense_mode=AttackDefenseMode.COUNTERATTACK,
    ) == AttackResolution.COUNTERATTACK_SUCCESS
    assert evaluate_melee_attack_resolution(
        actor_roll=draw_roll,
        defender_roll=draw_roll,
        defense_mode=AttackDefenseMode.DODGE,
    ) == AttackResolution.KP_REVIEW
    assert evaluate_melee_attack_resolution(
        actor_roll=double_failure_roll,
        defender_roll=double_failure_roll,
        defense_mode=AttackDefenseMode.DODGE,
    ) == AttackResolution.MISS
    assert evaluate_ranged_attack_resolution(actor_roll) == AttackResolution.HIT
    assert evaluate_ranged_attack_resolution(double_failure_roll) == AttackResolution.MISS


def test_damage_expression_helpers_support_stable_db_and_static_modifier_combinations() -> None:
    assert compute_damage_bonus_expression(strength=50, size=60) == "0"
    assert compute_damage_bonus_expression(strength=70, size=60) == "1d4"
    assert compute_damage_bonus_expression(strength=95, size=80) == "1d6"
    assert roll_damage_expression("1d1+db", db_expression="1d1", seed=7) == 2
    assert roll_damage_expression("1d1+2", seed=7) == 3


def test_hit_location_and_heavy_wound_helpers_keep_authoritative_combat_consequences_local() -> None:
    assert resolve_hit_location(1) == HitLocation.RIGHT_LEG
    assert resolve_hit_location(12) == HitLocation.CHEST
    assert resolve_hit_location(20) == HitLocation.HEAD

    assert evaluate_heavy_wound(final_damage=5, max_hit_points=11) is False
    assert evaluate_heavy_wound(final_damage=6, max_hit_points=11) is True
