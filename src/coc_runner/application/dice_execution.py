from __future__ import annotations

from typing import Callable, Protocol

from pydantic import BaseModel, Field

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
