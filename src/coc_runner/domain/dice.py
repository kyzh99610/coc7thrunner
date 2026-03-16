from __future__ import annotations

import random
import re

from pydantic import BaseModel, Field

from coc_runner.compat import StrEnum


class RollOutcome(StrEnum):
    CRITICAL_SUCCESS = "critical_success"
    EXTREME_SUCCESS = "extreme_success"
    HARD_SUCCESS = "hard_success"
    SUCCESS = "success"
    FAILURE = "failure"
    FUMBLE = "fumble"


class OpposedCheckResolution(StrEnum):
    ACTOR_WIN = "actor_win"
    OPPONENT_WIN = "opponent_win"
    DRAW = "draw"
    DOUBLE_FAILURE = "double_failure"


class AttackDefenseMode(StrEnum):
    DODGE = "dodge"
    COUNTERATTACK = "counterattack"


class AttackResolution(StrEnum):
    HIT = "hit"
    MISS = "miss"
    DODGE_SUCCESS = "dodge_success"
    COUNTERATTACK_SUCCESS = "counterattack_success"
    KP_REVIEW = "kp_review"


class HitLocation(StrEnum):
    RIGHT_LEG = "right_leg"
    LEFT_LEG = "left_leg"
    ABDOMEN = "abdomen"
    CHEST = "chest"
    RIGHT_ARM = "right_arm"
    LEFT_ARM = "left_arm"
    HEAD = "head"


class WoundAftermath(BaseModel):
    heavy_wound: bool = False
    unconscious: bool = False
    dying: bool = False
    stable: bool = False
    fatal_risk: bool = False
    kp_follow_up_required: bool = False


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


def evaluate_opposed_rolls(
    actor_roll: D100Roll,
    opponent_roll: D100Roll,
) -> OpposedCheckResolution:
    actor_failed = actor_roll.outcome in {RollOutcome.FAILURE, RollOutcome.FUMBLE}
    opponent_failed = opponent_roll.outcome in {RollOutcome.FAILURE, RollOutcome.FUMBLE}
    if actor_failed and opponent_failed:
        return OpposedCheckResolution.DOUBLE_FAILURE
    if actor_failed:
        return OpposedCheckResolution.OPPONENT_WIN
    if opponent_failed:
        return OpposedCheckResolution.ACTOR_WIN

    outcome_priority = {
        RollOutcome.CRITICAL_SUCCESS: 5,
        RollOutcome.EXTREME_SUCCESS: 4,
        RollOutcome.HARD_SUCCESS: 3,
        RollOutcome.SUCCESS: 2,
        RollOutcome.FAILURE: 1,
        RollOutcome.FUMBLE: 0,
    }
    actor_priority = outcome_priority[actor_roll.outcome]
    opponent_priority = outcome_priority[opponent_roll.outcome]
    if actor_priority > opponent_priority:
        return OpposedCheckResolution.ACTOR_WIN
    if opponent_priority > actor_priority:
        return OpposedCheckResolution.OPPONENT_WIN
    return OpposedCheckResolution.DRAW


def evaluate_melee_attack_resolution(
    actor_roll: D100Roll,
    defender_roll: D100Roll,
    defense_mode: AttackDefenseMode,
) -> AttackResolution:
    opposed_resolution = evaluate_opposed_rolls(actor_roll, defender_roll)
    if opposed_resolution == OpposedCheckResolution.ACTOR_WIN:
        return AttackResolution.HIT
    if opposed_resolution == OpposedCheckResolution.OPPONENT_WIN:
        if defense_mode == AttackDefenseMode.COUNTERATTACK:
            return AttackResolution.COUNTERATTACK_SUCCESS
        return AttackResolution.DODGE_SUCCESS
    if opposed_resolution == OpposedCheckResolution.DRAW:
        return AttackResolution.KP_REVIEW
    return AttackResolution.MISS


def evaluate_ranged_attack_resolution(actor_roll: D100Roll) -> AttackResolution:
    if actor_roll.outcome in {RollOutcome.FAILURE, RollOutcome.FUMBLE}:
        return AttackResolution.MISS
    return AttackResolution.HIT


def resolve_hit_location(roll_value: int) -> HitLocation:
    if not 1 <= roll_value <= 20:
        raise ValueError("hit location roll must be between 1 and 20")
    if roll_value <= 4:
        return HitLocation.RIGHT_LEG
    if roll_value <= 8:
        return HitLocation.LEFT_LEG
    if roll_value <= 11:
        return HitLocation.ABDOMEN
    if roll_value == 12:
        return HitLocation.CHEST
    if roll_value <= 15:
        return HitLocation.RIGHT_ARM
    if roll_value <= 18:
        return HitLocation.LEFT_ARM
    return HitLocation.HEAD


def roll_hit_location(*, seed: int | None = None) -> tuple[int, HitLocation]:
    rng = random.Random(seed)
    roll_value = rng.randint(1, 20)
    return roll_value, resolve_hit_location(roll_value)


def evaluate_heavy_wound(*, final_damage: int, max_hit_points: int) -> bool:
    if final_damage < 0:
        raise ValueError("final_damage must be non-negative")
    if max_hit_points <= 0:
        raise ValueError("max_hit_points must be positive")
    return final_damage * 2 >= max_hit_points


def evaluate_wound_aftermath(
    *,
    final_damage: int,
    hp_after: int,
    max_hit_points: int,
) -> WoundAftermath:
    if hp_after < 0:
        raise ValueError("hp_after must be non-negative")
    heavy_wound = evaluate_heavy_wound(
        final_damage=final_damage,
        max_hit_points=max_hit_points,
    )
    unconscious = hp_after == 0
    dying = unconscious and heavy_wound
    stable = unconscious and not dying
    return WoundAftermath(
        heavy_wound=heavy_wound,
        unconscious=unconscious,
        dying=dying,
        stable=stable,
        fatal_risk=dying,
        kp_follow_up_required=heavy_wound or dying,
    )


def compute_damage_bonus_expression(*, strength: int, size: int) -> str:
    total = strength + size
    if total <= 64:
        return "-2"
    if total <= 84:
        return "-1"
    if total <= 124:
        return "0"
    if total <= 164:
        return "1d4"
    if total <= 204:
        return "1d6"
    extra_d6 = 2 + max(0, (total - 205) // 80)
    return f"{extra_d6}d6"


_DAMAGE_TERM_PATTERN = re.compile(r"([+-]?)(db|\d+d\d+|\d+)")


def roll_damage_expression(
    expression: str,
    *,
    db_expression: str | None = None,
    seed: int | None = None,
) -> int:
    rng = random.Random(seed)
    total = _evaluate_damage_expression(
        expression,
        db_expression=db_expression,
        rng=rng,
        allow_db_term=True,
    )
    return max(0, total)


def _evaluate_damage_expression(
    expression: str,
    *,
    db_expression: str | None,
    rng: random.Random,
    allow_db_term: bool,
) -> int:
    normalized = expression.replace(" ", "").lower()
    if not normalized:
        raise ValueError("damage expression must not be empty")
    total = 0
    current_index = 0
    for match in _DAMAGE_TERM_PATTERN.finditer(normalized):
        if match.start() != current_index:
            raise ValueError(f"unsupported damage expression: {expression}")
        sign_token, term = match.groups()
        sign = -1 if sign_token == "-" else 1
        term_value = _resolve_damage_term(
            term,
            db_expression=db_expression,
            rng=rng,
            allow_db_term=allow_db_term,
        )
        total += sign * term_value
        current_index = match.end()
    if current_index != len(normalized):
        raise ValueError(f"unsupported damage expression: {expression}")
    return total


def _resolve_damage_term(
    term: str,
    *,
    db_expression: str | None,
    rng: random.Random,
    allow_db_term: bool,
) -> int:
    if term == "db":
        if not allow_db_term or db_expression is None:
            raise ValueError("damage expression references db without a resolved damage bonus")
        return _evaluate_damage_expression(
            db_expression,
            db_expression=None,
            rng=rng,
            allow_db_term=False,
        )
    if "d" in term:
        count_text, sides_text = term.split("d", maxsplit=1)
        count = int(count_text)
        sides = int(sides_text)
        if count <= 0 or sides <= 0:
            raise ValueError("damage dice counts and sides must be positive")
        return sum(rng.randint(1, sides) for _ in range(count))
    return int(term)


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
