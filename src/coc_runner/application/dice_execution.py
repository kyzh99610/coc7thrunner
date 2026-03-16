from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Protocol

from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError

from coc_runner.compat import StrEnum
from coc_runner.domain.dice import (
    D100Roll,
    OpposedCheckResolution,
    evaluate_opposed_rolls,
    roll_d100,
)
from coc_runner.domain.models import LanguagePreference


class DiceCheckKind(StrEnum):
    SKILL = "skill"
    ATTRIBUTE = "attribute"
    SANITY = "sanity"
    OPPOSED = "opposed"
    ATTACK_MELEE = "attack_melee"
    ATTACK_RANGED = "attack_ranged"


class DiceExecutionRequest(BaseModel):
    session_id: str
    actor_id: str
    check_kind: DiceCheckKind
    label: str = Field(min_length=1, max_length=120)
    target_value: int = Field(ge=1, le=100)
    language_preference: LanguagePreference = LanguagePreference.ZH_CN
    seed: int | None = None
    bonus_dice: int = Field(default=0, ge=0)
    penalty_dice: int = Field(default=0, ge=0)
    pushed: bool = False
    opposed_label: str | None = Field(default=None, min_length=1, max_length=120)
    opposed_target_value: int | None = Field(default=None, ge=1, le=100)
    opposed_bonus_dice: int = Field(default=0, ge=0)
    opposed_penalty_dice: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_opposed_shape(self) -> "DiceExecutionRequest":
        if self.bonus_dice and self.penalty_dice:
            raise ValueError("bonus_dice and penalty_dice cannot both be non-zero")
        if self.opposed_bonus_dice and self.opposed_penalty_dice:
            raise ValueError(
                "opposed_bonus_dice and opposed_penalty_dice cannot both be non-zero"
            )
        has_opposed_fields = (
            self.opposed_label is not None
            or self.opposed_target_value is not None
            or self.opposed_bonus_dice > 0
            or self.opposed_penalty_dice > 0
        )
        if self.check_kind in {DiceCheckKind.OPPOSED, DiceCheckKind.ATTACK_MELEE}:
            if self.opposed_label is None or self.opposed_target_value is None:
                raise ValueError(
                    "opposed checks require both opposed_label and opposed_target_value"
                )
        elif has_opposed_fields:
            raise ValueError(
                "opposed-only execution fields are not supported for this check kind"
            )
        return self


class DiceExecutionResult(BaseModel):
    backend_name: str = Field(min_length=1, max_length=80)
    roll: D100Roll
    success: bool
    pushed: bool = False
    opposed_roll: D100Roll | None = None
    opposed_label: str | None = Field(default=None, min_length=1, max_length=120)
    opposed_resolution: OpposedCheckResolution | None = None


class DiceStyleSubprocessPayload(BaseModel):
    request: DiceExecutionRequest
    command_text: str = Field(min_length=1, max_length=240)


class DiceExecutionError(RuntimeError):
    """Base error for optional dice execution backends."""


class DiceExecutionUnavailableError(DiceExecutionError):
    """Raised when an optional dice execution backend is currently unavailable."""


class UnsupportedDiceCheckError(DiceExecutionError):
    """Raised when a backend cannot safely execute a specific check kind."""


class DiceExecutionBackend(Protocol):
    backend_name: str

    def execute_check(self, request: DiceExecutionRequest) -> DiceExecutionResult:
        ...


class DiceStyleSidecarClient(Protocol):
    def execute_check(
        self,
        *,
        request: DiceExecutionRequest,
        command_text: str,
    ) -> DiceExecutionResult:
        ...


class LocalDiceExecutionBackend:
    backend_name = "local"

    def __init__(
        self,
        *,
        roller: Callable[..., D100Roll] = roll_d100,
    ) -> None:
        self._roller = roller

    def execute_check(self, request: DiceExecutionRequest) -> DiceExecutionResult:
        roll = self._roller(
            request.target_value,
            seed=request.seed,
            bonus_dice=request.bonus_dice,
            penalty_dice=request.penalty_dice,
        )
        if request.check_kind in {DiceCheckKind.OPPOSED, DiceCheckKind.ATTACK_MELEE}:
            opponent_seed = request.seed + 1 if request.seed is not None else None
            opposed_roll = self._roller(
                request.opposed_target_value or request.target_value,
                seed=opponent_seed,
                bonus_dice=request.opposed_bonus_dice,
                penalty_dice=request.opposed_penalty_dice,
            )
            resolution = evaluate_opposed_rolls(roll, opposed_roll)
            return DiceExecutionResult(
                backend_name=self.backend_name,
                roll=roll,
                success=resolution == OpposedCheckResolution.ACTOR_WIN,
                pushed=request.pushed,
                opposed_roll=opposed_roll,
                opposed_label=request.opposed_label,
                opposed_resolution=resolution,
            )
        return DiceExecutionResult(
            backend_name=self.backend_name,
            roll=roll,
            success=roll.total <= request.target_value,
            pushed=request.pushed,
        )


class DiceStyleSubprocessClient:
    def __init__(
        self,
        *,
        command: list[str],
        timeout_seconds: float = 3.0,
    ) -> None:
        if not command:
            raise ValueError("dice subprocess command must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("dice subprocess timeout must be positive")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def execute_check(
        self,
        *,
        request: DiceExecutionRequest,
        command_text: str,
    ) -> DiceExecutionResult:
        payload = DiceStyleSubprocessPayload(
            request=request,
            command_text=command_text,
        )
        try:
            environment = os.environ.copy()
            environment.setdefault("PYTHONIOENCODING", "utf-8")
            completed = subprocess.run(
                self.command,
                input=payload.model_dump_json(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DiceExecutionUnavailableError(
                f"dice subprocess is unavailable: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DiceExecutionUnavailableError(
                "dice subprocess timed out before returning a result"
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            raise DiceExecutionUnavailableError(
                f"dice subprocess exited with code {completed.returncode}{detail}"
            )
        stdout = completed.stdout.strip()
        try:
            parsed = json.loads(stdout)
            return DiceExecutionResult.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DiceExecutionUnavailableError(
                "dice subprocess returned invalid result payload"
            ) from exc


class DiceStyleExecutionBackend:
    backend_name = "dice_style"

    def __init__(
        self,
        *,
        client: DiceStyleSidecarClient,
        fallback_backend: DiceExecutionBackend | None = None,
    ) -> None:
        self._client = client
        self._fallback_backend = fallback_backend or LocalDiceExecutionBackend()

    def execute_check(self, request: DiceExecutionRequest) -> DiceExecutionResult:
        try:
            command_text = render_dice_style_command(request)
            result = self._client.execute_check(
                request=request,
                command_text=command_text,
            )
            if result.backend_name:
                return result
            return result.model_copy(update={"backend_name": self.backend_name})
        except (DiceExecutionUnavailableError, UnsupportedDiceCheckError):
            if self._fallback_backend is not None:
                return self._fallback_backend.execute_check(request)
            raise


def render_dice_style_command(request: DiceExecutionRequest) -> str:
    if request.check_kind == DiceCheckKind.SANITY:
        raise UnsupportedDiceCheckError(
            "sanity checks require authoritative local SAN state and are not forwarded to Dice-style commands"
        )
    normalized_label = " ".join(request.label.split()).strip()
    if not normalized_label:
        raise ValueError("dice execution label must not be blank")
    primary_expression = _render_dice_style_check_expression(
        label=normalized_label,
        target_value=request.target_value,
        bonus_dice=request.bonus_dice,
        penalty_dice=request.penalty_dice,
    )
    if request.check_kind in {DiceCheckKind.OPPOSED, DiceCheckKind.ATTACK_MELEE}:
        if request.opposed_label is None or request.opposed_target_value is None:
            raise UnsupportedDiceCheckError(
                "opposed checks require explicit opponent label and target value"
            )
        primary_expression = _render_dice_style_opposed_expression(
            label=normalized_label,
            target_value=request.target_value,
            bonus_dice=request.bonus_dice,
            penalty_dice=request.penalty_dice,
        )
        opposed_expression = _render_dice_style_opposed_expression(
            label=request.opposed_label,
            target_value=request.opposed_target_value,
            bonus_dice=request.opposed_bonus_dice,
            penalty_dice=request.opposed_penalty_dice,
        )
        return f".rav {primary_expression} {opposed_expression}"
    return _render_single_dice_style_command(
        expression=primary_expression,
        bonus_dice=request.bonus_dice,
        penalty_dice=request.penalty_dice,
    )


def _render_single_dice_style_command(
    *,
    expression: str,
    bonus_dice: int,
    penalty_dice: int,
) -> str:
    if bonus_dice or penalty_dice:
        return f".ra {expression}"
    return f".rc {expression}"


def _render_dice_style_check_expression(
    *,
    label: str,
    target_value: int,
    bonus_dice: int,
    penalty_dice: int,
) -> str:
    if bonus_dice and penalty_dice:
        raise UnsupportedDiceCheckError(
            "bonus and penalty dice cannot both be forwarded to the Dice-style bridge"
        )
    normalized_label = " ".join(label.split()).strip()
    if not normalized_label:
        raise ValueError("dice execution label must not be blank")
    if bonus_dice:
        return f"b{bonus_dice} {normalized_label}{target_value}"
    if penalty_dice:
        return f"p{penalty_dice} {normalized_label}{target_value}"
    return f"{normalized_label}{target_value}"


def _render_dice_style_opposed_expression(
    *,
    label: str,
    target_value: int,
    bonus_dice: int,
    penalty_dice: int,
) -> str:
    if bonus_dice and penalty_dice:
        raise UnsupportedDiceCheckError(
            "bonus and penalty dice cannot both be forwarded to the Dice-style bridge"
        )
    normalized_label = " ".join(label.split()).strip()
    if not normalized_label:
        raise ValueError("dice execution label must not be blank")
    expression = f"{normalized_label}{target_value}"
    if bonus_dice:
        return f"{expression},b{bonus_dice}"
    if penalty_dice:
        return f"{expression},p{penalty_dice}"
    return expression


def build_default_dice_style_subprocess_command(
    provider_command: list[str] | None = None,
) -> list[str]:
    bridge_module_path = Path(__file__).with_name("dice_style_subprocess_bridge.py")
    command = [sys.executable, str(bridge_module_path)]
    if provider_command:
        command.extend(
            [
                "--provider-command-json",
                json.dumps(provider_command, ensure_ascii=False),
            ]
        )
    return command
