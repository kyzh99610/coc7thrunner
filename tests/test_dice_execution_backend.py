from __future__ import annotations

from coc_runner.application.dice_execution import (
    DiceCheckKind,
    DiceExecutionRequest,
    DiceExecutionResult,
    DiceExecutionUnavailableError,
    DiceStyleExecutionBackend,
    LocalDiceExecutionBackend,
    UnsupportedDiceCheckError,
    render_dice_style_command,
)
from coc_runner.domain.dice import D100Roll, RollOutcome
from coc_runner.domain.models import LanguagePreference


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


class _FakeDiceStyleClient:
    def __init__(self, result: DiceExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[DiceExecutionRequest, str]] = []

    def execute_check(
        self,
        *,
        request: DiceExecutionRequest,
        command_text: str,
    ) -> DiceExecutionResult:
        self.calls.append((request, command_text))
        return self.result


class _UnavailableDiceStyleClient:
    def __init__(self) -> None:
        self.calls: list[tuple[DiceExecutionRequest, str]] = []

    def execute_check(
        self,
        *,
        request: DiceExecutionRequest,
        command_text: str,
    ) -> DiceExecutionResult:
        self.calls.append((request, command_text))
        raise DiceExecutionUnavailableError("sidecar unavailable")


class _FixedFallbackBackend:
    backend_name = "local_fallback"

    def __init__(self, result: DiceExecutionResult) -> None:
        self.result = result
        self.calls: list[DiceExecutionRequest] = []

    def execute_check(self, request: DiceExecutionRequest) -> DiceExecutionResult:
        self.calls.append(request)
        return self.result


def test_local_dice_execution_backend_rolls_from_internal_contract_deterministically() -> None:
    backend = LocalDiceExecutionBackend()
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
        seed=42,
    )

    first = backend.execute_check(request)
    second = backend.execute_check(request)

    assert first.backend_name == "local"
    assert first.success == second.success
    assert first.roll.total == second.roll.total
    assert first.roll.tens_dice == second.roll.tens_dice
    assert first.roll.target == 70


def test_dice_style_backend_renders_command_in_adapter_layer_and_delegates_skill_check() -> None:
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
    )
    client = _FakeDiceStyleClient(
        DiceExecutionResult(
            backend_name="dice_style",
            roll=_build_roll(total=24, target=70, outcome=RollOutcome.HARD_SUCCESS),
            success=True,
        )
    )
    backend = DiceStyleExecutionBackend(client=client)

    result = backend.execute_check(request)

    assert render_dice_style_command(request) == ".rc 图书馆使用70"
    assert result.backend_name == "dice_style"
    assert result.roll.total == 24
    assert result.success is True
    assert len(client.calls) == 1
    delegated_request, command_text = client.calls[0]
    assert delegated_request.check_kind == DiceCheckKind.SKILL
    assert delegated_request.target_value == 70
    assert command_text == ".rc 图书馆使用70"


def test_dice_style_backend_falls_back_when_sidecar_unavailable_or_check_not_supported() -> None:
    unavailable_client = _UnavailableDiceStyleClient()
    fallback_backend = _FixedFallbackBackend(
        DiceExecutionResult(
            backend_name="local_fallback",
            roll=_build_roll(total=55, target=70, outcome=RollOutcome.SUCCESS),
            success=True,
        )
    )
    bridge = DiceStyleExecutionBackend(
        client=unavailable_client,
        fallback_backend=fallback_backend,
    )
    skill_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="侦查",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
    )

    skill_result = bridge.execute_check(skill_request)

    assert skill_result.backend_name == "local_fallback"
    assert skill_result.roll.total == 55
    assert len(unavailable_client.calls) == 1
    assert unavailable_client.calls[0][1] == ".rc 侦查70"
    assert fallback_backend.calls == [skill_request]

    fallback_backend.calls.clear()
    sanity_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SANITY,
        label="黄衣之王的近距离显现",
        target_value=60,
        language_preference=LanguagePreference.ZH_CN,
    )

    sanity_result = bridge.execute_check(sanity_request)

    assert sanity_result.backend_name == "local_fallback"
    assert len(unavailable_client.calls) == 1
    assert fallback_backend.calls == [sanity_request]
    with_exception = False
    try:
        render_dice_style_command(sanity_request)
    except UnsupportedDiceCheckError:
        with_exception = True
    assert with_exception is True
