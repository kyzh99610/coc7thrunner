from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Protocol

from pydantic import BaseModel, Field
from pydantic import ValidationError

from coc_runner.compat import StrEnum
from coc_runner.domain.dice import D100Roll, roll_d100
from coc_runner.domain.models import LanguagePreference


class DiceCheckKind(StrEnum):
    SKILL = "skill"
    ATTRIBUTE = "attribute"
    SANITY = "sanity"


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


class DiceExecutionResult(BaseModel):
    backend_name: str = Field(min_length=1, max_length=80)
    roll: D100Roll
    success: bool


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
        return DiceExecutionResult(
            backend_name=self.backend_name,
            roll=roll,
            success=roll.total <= request.target_value,
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
    if request.bonus_dice or request.penalty_dice:
        raise UnsupportedDiceCheckError(
            "bonus/penalty dice are not wired into the Dice-style bridge in this MVP"
        )
    normalized_label = " ".join(request.label.split()).strip()
    if not normalized_label:
        raise ValueError("dice execution label must not be blank")
    return f".rc {normalized_label}{request.target_value}"


def build_default_dice_style_subprocess_command() -> list[str]:
    bridge_module_path = Path(__file__).with_name("dice_style_subprocess_bridge.py")
    return [sys.executable, str(bridge_module_path)]
