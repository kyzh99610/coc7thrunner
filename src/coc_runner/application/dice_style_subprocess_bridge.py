from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from coc_runner.application.dice_execution import (  # noqa: E402
    DiceCheckKind,
    DiceExecutionResult,
    DiceStyleSubprocessPayload,
)
from coc_runner.domain.dice import (  # noqa: E402
    D100Roll,
    OpposedCheckResolution,
    RollOutcome,
    evaluate_d100_roll,
    evaluate_opposed_rolls,
)

_STANDARD_DICE_RESULT_PATTERN = re.compile(
    r"D100\s*=\s*(?P<rolled>\d{1,3})\s*/\s*(?P<target>\d{1,3})\s*(?P<rank>大成功|极难成功|困难成功|成功|失败|大失败)!?"
)
_MODIFIED_DICE_RESULT_PATTERN = re.compile(
    r"(?P<mode>[bp])(?P<count>\d*)\s*=\s*(?P<rolled>\d{1,3})\s*/\s*(?P<target>\d{1,3}),\s*"
    r"\(\[D100=(?P<base_total>\d{1,3}),\s*(?P<label>奖励|惩罚)\s+(?P<extra_tens>\d(?:\s+\d)*)\]\)\s*"
    r"(?P<rank>大成功|极难成功|困难成功|成功|失败|大失败)!?"
)
_OPPOSED_DICE_RESULT_PATTERN = re.compile(
    r"对抗检定:\s*"
    r"(?P<actor_label>.+?)\s*->\s*属性值:(?P<actor_target>\d{1,3})\s*判定值:(?P<actor_roll>\d{1,3})(?:\[\[.*?\]\])?\s*(?P<actor_rank>大成功|极难成功|困难成功|成功|失败|大失败)!?\s+"
    r"(?P<opponent_label>.+?)\s*->\s*属性值:(?P<opponent_target>\d{1,3})\s*判定值:(?P<opponent_roll>\d{1,3})(?:\[\[.*?\]\])?\s*(?P<opponent_rank>大成功|极难成功|困难成功|成功|失败|大失败)!?\s+(?P<summary>.+)"
)

def _configure_stdio() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _build_roll(*, total: int, target: int, outcome: RollOutcome) -> D100Roll:
    selected_tens = 0 if total == 100 else total // 10
    unit_die = 0 if total == 100 else total % 10
    return D100Roll(
        unit_die=unit_die,
        tens_dice=[selected_tens],
        selected_tens=selected_tens,
        total=total,
        target=target,
        outcome=outcome,
    )


def _build_modified_roll(
    *,
    total: int,
    target: int,
    base_total: int,
    extra_tens: list[int],
    bonus_dice: int,
    penalty_dice: int,
) -> D100Roll:
    selected_tens = 0 if total == 100 else total // 10
    unit_die = 0 if total == 100 else total % 10
    base_selected_tens = 0 if base_total == 100 else base_total // 10
    base_unit_die = 0 if base_total == 100 else base_total % 10
    if base_unit_die != unit_die:
        raise ValueError("Dice-style provider returned inconsistent unit die in modified roll output")
    return D100Roll(
        unit_die=unit_die,
        tens_dice=[base_selected_tens, *extra_tens],
        selected_tens=selected_tens,
        total=total,
        target=target,
        bonus_dice=bonus_dice,
        penalty_dice=penalty_dice,
        outcome=evaluate_d100_roll(total, target),
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dice-style subprocess bridge")
    parser.add_argument(
        "--provider-command-json",
        help="JSON array describing the real Dice-style provider command",
    )
    return parser.parse_args(argv)


def _load_payload() -> DiceStyleSubprocessPayload:
    raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        raise ValueError("missing dice subprocess payload")
    return DiceStyleSubprocessPayload.model_validate_json(raw_payload)


def _load_provider_command(provider_command_json: str | None) -> list[str]:
    if not provider_command_json:
        raise ValueError("missing Dice-style provider command configuration")
    parsed = json.loads(provider_command_json)
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise ValueError("provider command must be a JSON string array")
    return parsed


def _execute_provider_command(provider_command: list[str], *, command_text: str) -> str:
    environment = os.environ.copy()
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        provider_command,
        input=command_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(
            f"Dice-style provider exited with code {completed.returncode}{detail}"
        )
    return completed.stdout.strip()


def _parse_provider_output(
    *,
    raw_output: str,
    payload: DiceStyleSubprocessPayload,
) -> DiceExecutionResult:
    if payload.request.check_kind == DiceCheckKind.OPPOSED:
        return _parse_opposed_provider_output(raw_output=raw_output, payload=payload)
    modified_match = _MODIFIED_DICE_RESULT_PATTERN.search(raw_output)
    if modified_match is not None:
        rolled_value = int(modified_match.group("rolled"))
        target_value = int(modified_match.group("target"))
        if target_value != payload.request.target_value:
            raise ValueError(
                f"Dice-style provider target mismatch: expected {payload.request.target_value}, got {target_value}"
            )
        extra_tens = [
            int(item)
            for item in modified_match.group("extra_tens").split()
            if item
        ]
        mode = modified_match.group("mode")
        roll = _build_modified_roll(
            total=rolled_value,
            target=target_value,
            base_total=int(modified_match.group("base_total")),
            extra_tens=extra_tens,
            bonus_dice=len(extra_tens) if mode == "b" else 0,
            penalty_dice=len(extra_tens) if mode == "p" else 0,
        )
        return DiceExecutionResult(
            backend_name="dice_style_real_subprocess",
            roll=roll,
            success=roll.outcome not in {RollOutcome.FAILURE, RollOutcome.FUMBLE},
            pushed=payload.request.pushed,
        )

    standard_match = _STANDARD_DICE_RESULT_PATTERN.search(raw_output)
    if standard_match is None:
        raise ValueError("Dice-style provider output did not contain a parseable D100 result")
    rolled_value = int(standard_match.group("rolled"))
    target_value = int(standard_match.group("target"))
    if target_value != payload.request.target_value:
        raise ValueError(
            f"Dice-style provider target mismatch: expected {payload.request.target_value}, got {target_value}"
        )
    roll = _build_roll(
        total=rolled_value,
        target=target_value,
        outcome=evaluate_d100_roll(rolled_value, target_value),
    )
    return DiceExecutionResult(
        backend_name="dice_style_real_subprocess",
        roll=roll,
        success=roll.outcome not in {RollOutcome.FAILURE, RollOutcome.FUMBLE},
        pushed=payload.request.pushed,
    )


def _parse_opposed_provider_output(
    *,
    raw_output: str,
    payload: DiceStyleSubprocessPayload,
) -> DiceExecutionResult:
    match = _OPPOSED_DICE_RESULT_PATTERN.search(raw_output)
    if match is None:
        raise ValueError("Dice-style provider output did not contain a parseable opposed result")
    actor_target = int(match.group("actor_target"))
    opponent_target = int(match.group("opponent_target"))
    if actor_target != payload.request.target_value:
        raise ValueError(
            f"Dice-style provider actor target mismatch: expected {payload.request.target_value}, got {actor_target}"
        )
    expected_opponent_target = payload.request.opposed_target_value
    if expected_opponent_target is None:
        raise ValueError("opposed request is missing opponent target value")
    if opponent_target != expected_opponent_target:
        raise ValueError(
            f"Dice-style provider opponent target mismatch: expected {expected_opponent_target}, got {opponent_target}"
        )
    actor_roll = _build_roll(
        total=int(match.group("actor_roll")),
        target=actor_target,
        outcome=evaluate_d100_roll(int(match.group("actor_roll")), actor_target),
    )
    opponent_roll = _build_roll(
        total=int(match.group("opponent_roll")),
        target=opponent_target,
        outcome=evaluate_d100_roll(int(match.group("opponent_roll")), opponent_target),
    )
    resolution = evaluate_opposed_rolls(actor_roll, opponent_roll)
    return DiceExecutionResult(
        backend_name="dice_style_real_subprocess",
        roll=actor_roll,
        success=resolution == OpposedCheckResolution.ACTOR_WIN,
        pushed=payload.request.pushed,
        opposed_roll=opponent_roll,
        opposed_label=payload.request.opposed_label,
        opposed_resolution=resolution,
    )


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = _parse_args(argv or sys.argv[1:])
    try:
        payload = _load_payload()
        provider_command = _load_provider_command(args.provider_command_json)
        raw_output = _execute_provider_command(
            provider_command,
            command_text=payload.command_text,
        )
        result = _parse_provider_output(
            raw_output=raw_output,
            payload=payload,
        )
    except (ValidationError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 2

    sys.stdout.write(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
