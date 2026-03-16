from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from coc_runner.application.dice_execution import (
    DiceCheckKind,
    DiceExecutionRequest,
    DiceExecutionResult,
    DiceExecutionUnavailableError,
    DiceStyleExecutionBackend,
    DiceStyleSubprocessClient,
    LocalDiceExecutionBackend,
    UnsupportedDiceCheckError,
    build_default_dice_style_subprocess_command,
    render_dice_style_command,
)
from coc_runner.config import Settings
from coc_runner.domain.dice import D100Roll, OpposedCheckResolution, RollOutcome
from coc_runner.domain.models import LanguagePreference
from coc_runner.main import create_app


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dice_subprocess"
BRIDGE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "coc_runner"
    / "application"
    / "dice_style_subprocess_bridge.py"
)


def _bridge_command(provider_script_name: str) -> list[str]:
    return [
        sys.executable,
        str(BRIDGE_SCRIPT),
        "--provider-command-json",
        json.dumps(
            [sys.executable, str(FIXTURE_DIR / provider_script_name)],
            ensure_ascii=False,
        ),
    ]


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


def test_render_dice_style_command_supports_common_bonus_and_penalty_variants() -> None:
    skill_bonus_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
        bonus_dice=2,
    )
    skill_penalty_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
        penalty_dice=1,
    )
    attribute_bonus_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.ATTRIBUTE,
        label="教育",
        target_value=75,
        language_preference=LanguagePreference.ZH_CN,
        bonus_dice=1,
    )
    attribute_penalty_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.ATTRIBUTE,
        label="教育",
        target_value=75,
        language_preference=LanguagePreference.ZH_CN,
        penalty_dice=2,
    )

    assert render_dice_style_command(skill_bonus_request) == ".ra b2 图书馆使用70"
    assert render_dice_style_command(skill_penalty_request) == ".ra p1 图书馆使用70"
    assert render_dice_style_command(attribute_bonus_request) == ".ra b1 教育75"
    assert render_dice_style_command(attribute_penalty_request) == ".ra p2 教育75"


def test_render_dice_style_command_supports_opposed_execution_and_keeps_pushed_as_internal_semantics() -> None:
    pushed_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
        pushed=True,
    )
    opposed_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="话术",
        target_value=50,
        opposed_label="守卫意志",
        opposed_target_value=40,
        language_preference=LanguagePreference.ZH_CN,
    )

    assert render_dice_style_command(pushed_request) == ".rc 图书馆使用70"
    assert render_dice_style_command(opposed_request) == ".rav 话术50 守卫意志40"


def test_render_dice_style_command_supports_official_like_opposed_modifier_suffixes() -> None:
    actor_bonus_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="潜行",
        target_value=20,
        opposed_label="守卫侦查",
        opposed_target_value=80,
        bonus_dice=1,
        language_preference=LanguagePreference.ZH_CN,
    )
    opponent_penalty_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="力量",
        target_value=60,
        opposed_label="守卫力量",
        opposed_target_value=60,
        opposed_penalty_dice=1,
        language_preference=LanguagePreference.ZH_CN,
    )

    assert render_dice_style_command(actor_bonus_request) == ".rav 潜行20,b1 守卫侦查80"
    assert render_dice_style_command(opponent_penalty_request) == ".rav 力量60 守卫力量60,p1"


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


def test_dice_style_subprocess_client_executes_real_external_bridge_process() -> None:
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
    )
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )

    result = client.execute_check(
        request=request,
        command_text=".rc 图书馆使用70",
    )

    assert result.backend_name == "dice_style_real_subprocess"
    assert result.roll.total == 24
    assert result.success is True


def test_dice_style_subprocess_client_parses_bonus_and_penalty_provider_outputs() -> None:
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )
    skill_bonus_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
        bonus_dice=2,
    )
    attribute_penalty_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.ATTRIBUTE,
        label="教育",
        target_value=75,
        language_preference=LanguagePreference.ZH_CN,
        penalty_dice=2,
    )

    skill_bonus_result = client.execute_check(
        request=skill_bonus_request,
        command_text=render_dice_style_command(skill_bonus_request),
    )
    attribute_penalty_result = client.execute_check(
        request=attribute_penalty_request,
        command_text=render_dice_style_command(attribute_penalty_request),
    )

    assert skill_bonus_result.roll.total == 15
    assert skill_bonus_result.roll.bonus_dice == 2
    assert skill_bonus_result.roll.penalty_dice == 0
    assert skill_bonus_result.roll.outcome == RollOutcome.HARD_SUCCESS
    assert skill_bonus_result.success is True

    assert attribute_penalty_result.roll.total == 95
    assert attribute_penalty_result.roll.bonus_dice == 0
    assert attribute_penalty_result.roll.penalty_dice == 2
    assert attribute_penalty_result.roll.outcome == RollOutcome.FAILURE
    assert attribute_penalty_result.success is False


def test_dice_style_subprocess_client_normalizes_provider_rank_back_to_local_official_outcome() -> None:
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="话术",
        target_value=50,
        language_preference=LanguagePreference.ZH_CN,
        bonus_dice=1,
    )

    result = client.execute_check(
        request=request,
        command_text=render_dice_style_command(request),
    )

    assert result.roll.total == 24
    assert result.roll.outcome == RollOutcome.HARD_SUCCESS
    assert result.success is True


def test_dice_style_subprocess_client_marks_pushed_result_without_leaking_it_to_provider_command_text() -> None:
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
        pushed=True,
    )

    result = client.execute_check(
        request=request,
        command_text=render_dice_style_command(request),
    )

    assert result.roll.total == 24
    assert result.pushed is True


def test_dice_style_subprocess_client_parses_opposed_provider_output_and_normalizes_resolution() -> None:
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )
    actor_win_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="话术",
        target_value=50,
        opposed_label="守卫意志",
        opposed_target_value=40,
        language_preference=LanguagePreference.ZH_CN,
    )
    draw_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="力量",
        target_value=60,
        opposed_label="守卫力量",
        opposed_target_value=60,
        language_preference=LanguagePreference.ZH_CN,
    )
    double_failure_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="侦查",
        target_value=55,
        opposed_label="守卫潜行",
        opposed_target_value=55,
        language_preference=LanguagePreference.ZH_CN,
    )

    actor_win_result = client.execute_check(
        request=actor_win_request,
        command_text=render_dice_style_command(actor_win_request),
    )
    draw_result = client.execute_check(
        request=draw_request,
        command_text=render_dice_style_command(draw_request),
    )
    double_failure_result = client.execute_check(
        request=double_failure_request,
        command_text=render_dice_style_command(double_failure_request),
    )

    assert actor_win_result.roll.total == 24
    assert actor_win_result.opposed_roll is not None
    assert actor_win_result.opposed_roll.total == 61
    assert actor_win_result.opposed_resolution == OpposedCheckResolution.ACTOR_WIN
    assert actor_win_result.success is True

    assert draw_result.opposed_roll is not None
    assert draw_result.roll.outcome == RollOutcome.SUCCESS
    assert draw_result.opposed_roll.outcome == RollOutcome.SUCCESS
    assert draw_result.opposed_resolution == OpposedCheckResolution.DRAW
    assert draw_result.success is False

    assert double_failure_result.opposed_roll is not None
    assert double_failure_result.roll.outcome == RollOutcome.FAILURE
    assert double_failure_result.opposed_roll.outcome == RollOutcome.FAILURE
    assert double_failure_result.opposed_resolution == OpposedCheckResolution.DOUBLE_FAILURE
    assert double_failure_result.success is False


def test_dice_style_subprocess_client_parses_official_like_bonus_and_penalty_rav_outputs() -> None:
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )
    actor_bonus_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="潜行",
        target_value=20,
        opposed_label="守卫侦查",
        opposed_target_value=80,
        bonus_dice=1,
        language_preference=LanguagePreference.ZH_CN,
    )
    opponent_penalty_request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="力量",
        target_value=60,
        opposed_label="守卫力量",
        opposed_target_value=60,
        opposed_penalty_dice=1,
        language_preference=LanguagePreference.ZH_CN,
    )

    actor_bonus_result = client.execute_check(
        request=actor_bonus_request,
        command_text=render_dice_style_command(actor_bonus_request),
    )
    opponent_penalty_result = client.execute_check(
        request=opponent_penalty_request,
        command_text=render_dice_style_command(opponent_penalty_request),
    )

    assert actor_bonus_result.roll.total == 7
    assert actor_bonus_result.roll.bonus_dice == 1
    assert actor_bonus_result.roll.outcome == RollOutcome.HARD_SUCCESS
    assert actor_bonus_result.opposed_roll is not None
    assert actor_bonus_result.opposed_roll.total == 28
    assert actor_bonus_result.opposed_roll.outcome == RollOutcome.HARD_SUCCESS
    assert actor_bonus_result.opposed_resolution == OpposedCheckResolution.DRAW

    assert opponent_penalty_result.roll.total == 42
    assert opponent_penalty_result.roll.outcome == RollOutcome.SUCCESS
    assert opponent_penalty_result.opposed_roll is not None
    assert opponent_penalty_result.opposed_roll.total == 84
    assert opponent_penalty_result.opposed_roll.penalty_dice == 1
    assert opponent_penalty_result.opposed_roll.outcome == RollOutcome.FAILURE
    assert opponent_penalty_result.opposed_resolution == OpposedCheckResolution.ACTOR_WIN
    assert opponent_penalty_result.success is True


def test_dice_style_subprocess_client_keeps_opposed_resolution_authoritative_locally_even_if_provider_summary_disagrees() -> None:
    client = DiceStyleSubprocessClient(
        command=_bridge_command("scripted_dice_provider.py"),
        timeout_seconds=1.0,
    )
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.OPPOSED,
        label="话术",
        target_value=50,
        opposed_label="守卫意志",
        opposed_target_value=50,
        language_preference=LanguagePreference.ZH_CN,
    )

    result = client.execute_check(
        request=request,
        command_text=render_dice_style_command(request),
    )

    assert result.roll.total == 24
    assert result.roll.outcome == RollOutcome.HARD_SUCCESS
    assert result.opposed_roll is not None
    assert result.opposed_roll.total == 21
    assert result.opposed_roll.outcome == RollOutcome.HARD_SUCCESS
    assert result.opposed_resolution == OpposedCheckResolution.DRAW
    assert result.success is False


def test_dice_style_subprocess_client_reports_invalid_output_timeout_and_provider_failure() -> None:
    request = DiceExecutionRequest(
        session_id="session-1",
        actor_id="investigator-1",
        check_kind=DiceCheckKind.SKILL,
        label="图书馆使用",
        target_value=70,
        language_preference=LanguagePreference.ZH_CN,
    )

    invalid_client = DiceStyleSubprocessClient(
        command=_bridge_command("invalid_dice_provider.py"),
        timeout_seconds=1.0,
    )
    try:
        invalid_client.execute_check(
            request=request,
            command_text=".rc 图书馆使用70",
        )
        invalid_failed = False
    except DiceExecutionUnavailableError:
        invalid_failed = True
    assert invalid_failed is True

    timeout_client = DiceStyleSubprocessClient(
        command=_bridge_command("slow_dice_provider.py"),
        timeout_seconds=0.1,
    )
    try:
        timeout_client.execute_check(
            request=request,
            command_text=".rc 图书馆使用70",
        )
        timeout_failed = False
    except DiceExecutionUnavailableError:
        timeout_failed = True
    assert timeout_failed is True

    failing_client = DiceStyleSubprocessClient(
        command=_bridge_command("failing_dice_provider.py"),
        timeout_seconds=1.0,
    )
    try:
        failing_client.execute_check(
            request=request,
            command_text=".rc 图书馆使用70",
        )
        provider_failed = False
    except DiceExecutionUnavailableError:
        provider_failed = True
    assert provider_failed is True


def test_create_app_can_enable_dice_style_subprocess_backend_mode() -> None:
    db_path = Path("test-artifacts") / "dice_backend_mode_test.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    provider_command = [sys.executable, str(FIXTURE_DIR / "scripted_dice_provider.py")]
    app = create_app(
        Settings(
            db_url=f"sqlite:///{db_path}",
            dice_backend_mode="dice_style_subprocess",
            dice_subprocess_timeout_seconds=1.0,
            dice_style_provider_command=tuple(provider_command),
        )
    )

    with TestClient(app) as client:
        backend = client.app.state.session_service.dice_execution_backend

    assert isinstance(backend, DiceStyleExecutionBackend)
    assert isinstance(backend._client, DiceStyleSubprocessClient)  # noqa: SLF001
    assert backend._client.command == build_default_dice_style_subprocess_command(provider_command)  # noqa: SLF001
