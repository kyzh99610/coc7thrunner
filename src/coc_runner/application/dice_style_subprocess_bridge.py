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
    DiceExecutionResult,
    DiceStyleSubprocessPayload,
)
from coc_runner.domain.dice import D100Roll, RollOutcome  # noqa: E402

_DICE_RESULT_PATTERN = re.compile(
    r"D100\s*=\s*(?P<rolled>\d{1,3})\s*/\s*(?P<target>\d{1,3})\s*(?P<rank>大成功|极难成功|困难成功|成功|失败|大失败)"
)

_RANK_TO_OUTCOME = {
    "大成功": RollOutcome.CRITICAL_SUCCESS,
    "极难成功": RollOutcome.EXTREME_SUCCESS,
    "困难成功": RollOutcome.HARD_SUCCESS,
    "成功": RollOutcome.SUCCESS,
    "失败": RollOutcome.FAILURE,
    "大失败": RollOutcome.FUMBLE,
}


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
    expected_target: int,
) -> DiceExecutionResult:
    match = _DICE_RESULT_PATTERN.search(raw_output)
    if match is None:
        raise ValueError("Dice-style provider output did not contain a parseable D100 result")
    rolled_value = int(match.group("rolled"))
    target_value = int(match.group("target"))
    if target_value != expected_target:
        raise ValueError(
            f"Dice-style provider target mismatch: expected {expected_target}, got {target_value}"
        )
    rank = match.group("rank")
    outcome = _RANK_TO_OUTCOME[rank]
    roll = _build_roll(total=rolled_value, target=target_value, outcome=outcome)
    return DiceExecutionResult(
        backend_name="dice_style_real_subprocess",
        roll=roll,
        success=outcome not in {RollOutcome.FAILURE, RollOutcome.FUMBLE},
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
            expected_target=payload.request.target_value,
        )
    except (ValidationError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 2

    sys.stdout.write(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
