from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from coc_runner.internal_local_launcher import (
    DEFAULT_EXPERIMENTAL_DEMO_PATH,
    DEFAULT_HOST,
    LocalServiceManager,
    build_service_command,
    collect_environment_snapshot,
    probe_healthz,
    report_fatal_launcher_error,
    resolve_project_root,
    run_headless_smoke,
    _should_surface_windowed_fatal_error,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])


def _make_run_dir() -> Path:
    base_dir = PROJECT_ROOT / "test-artifacts"
    base_dir.mkdir(exist_ok=True)
    run_dir = base_dir / f"internal_launcher_test_{uuid4().hex}"
    run_dir.mkdir()
    return run_dir


def test_build_service_command_uses_uvicorn_factory_without_reload() -> None:
    command = build_service_command(
        service_python=Path(r"C:\Python312\python.exe"),
        host="127.0.0.1",
        port=8123,
    )

    assert command == [
        r"C:\Python312\python.exe",
        "-m",
        "uvicorn",
        "coc_runner.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
    ]
    assert "--reload" not in command


def test_collect_environment_snapshot_reports_real_app_entry_and_local_llm_config() -> None:
    env = os.environ.copy()
    env["COC_RUNNER_LOCAL_LLM_ENABLED"] = "1"
    env["COC_RUNNER_LOCAL_LLM_BASE_URL"] = "http://127.0.0.1:11434/v1"
    env["COC_RUNNER_LOCAL_LLM_MODEL"] = "stub-launcher-model"

    snapshot = collect_environment_snapshot(
        project_root=PROJECT_ROOT,
        env=env,
        service_python=Path(sys.executable),
    )

    assert snapshot.python_available is True
    assert snapshot.python_version
    assert snapshot.app_entry == "coc_runner.main:create_app"
    assert snapshot.app_importable is True
    assert snapshot.uvicorn_available is True
    assert snapshot.local_llm_enabled is True
    assert snapshot.local_llm_base_url == "http://127.0.0.1:11434/v1"
    assert snapshot.local_llm_model == "stub-launcher-model"
    assert snapshot.error == ""


def test_collect_environment_snapshot_returns_clear_error_when_service_python_missing() -> None:
    run_dir = _make_run_dir()
    try:
        snapshot = collect_environment_snapshot(
            project_root=PROJECT_ROOT,
            service_python=run_dir / "missing-python.exe",
        )

        assert snapshot.python_available is False
        assert snapshot.app_importable is False
        assert "未找到项目虚拟环境解释器" in snapshot.error
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_resolve_project_root_supports_frozen_dist_layout() -> None:
    run_dir = _make_run_dir()
    fake_root = run_dir / "fake-repo"
    fake_exe = fake_root / "build-artifacts" / "internal-launcher-exe" / "dist" / "CoCRunnerInternalLauncher.exe"
    fake_module = fake_root / "src" / "coc_runner" / "internal_local_launcher.py"
    try:
        (fake_root / "src" / "coc_runner").mkdir(parents=True)
        (fake_root / "pyproject.toml").write_text("[project]\nname='fake'\n", encoding="utf-8")
        (fake_root / "src" / "coc_runner" / "main.py").write_text("def create_app():\n    return None\n", encoding="utf-8")
        fake_exe.parent.mkdir(parents=True)
        fake_exe.write_text("", encoding="utf-8")
        fake_module.write_text("", encoding="utf-8")

        resolved = resolve_project_root(
            executable_path=fake_exe,
            module_path=fake_module,
            cwd=run_dir,
            frozen=True,
        )

        assert resolved == fake_root
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_local_service_manager_can_start_probe_and_stop_real_service() -> None:
    run_dir = _make_run_dir()
    port = _find_free_port()
    db_path = run_dir / "launcher-test.db"
    env = os.environ.copy()
    env["COC_RUNNER_DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        port=port,
        service_python=Path(sys.executable),
        base_env=env,
    )

    try:
        assert manager.start() is True
        assert manager.wait_until_running(timeout_seconds=15.0) is True
        running_snapshot = manager.poll()
        assert running_snapshot.status == "running"
        assert probe_healthz(manager.health_url) is True
        assert manager.stop() is True
        stopped_snapshot = manager.poll()
        assert stopped_snapshot.status == "not_running"
        assert probe_healthz(manager.health_url) is False
    finally:
        if manager.snapshot().owns_process:
            manager.stop()
        shutil.rmtree(run_dir, ignore_errors=True)


def test_run_headless_smoke_can_start_open_browser_and_stop_real_service() -> None:
    run_dir = _make_run_dir()
    port = _find_free_port()
    db_path = run_dir / "launcher-headless-smoke.db"
    env = os.environ.copy()
    env["COC_RUNNER_DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    try:
        exit_code, payload = run_headless_smoke(
            project_root=PROJECT_ROOT,
            service_python=Path(sys.executable),
            port=port,
            base_env=env,
        )

        assert exit_code == 0
        assert payload["success"] is True
        assert payload["start_called"] is True
        assert payload["wait_until_running"] is True
        assert payload["browser_opened"] is True
        assert payload["stop_called"] is True
        assert payload["status_after_start"] == "running"
        assert payload["status_after_stop"] == "not_running"
        assert payload["browser_urls"] == [payload["experimental_demo_url"]]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_report_fatal_launcher_error_writes_log_and_notifies_reporter() -> None:
    run_dir = _make_run_dir()
    reported: list[tuple[str, str]] = []
    try:
        log_path = report_fatal_launcher_error(
            RuntimeError("boom"),
            project_root=run_dir,
            reporter=lambda title, message: reported.append((title, message)),
        )

        assert log_path.exists() is True
        assert "RuntimeError: boom" in log_path.read_text(encoding="utf-8")
        assert reported
        assert "boom" in reported[0][1]
        assert str(log_path) in reported[0][1]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_should_surface_windowed_fatal_error_only_for_frozen_process_without_console() -> None:
    assert _should_surface_windowed_fatal_error(frozen=True, stdout=None, stderr=None) is True
    assert _should_surface_windowed_fatal_error(frozen=True, stdout=object(), stderr=object()) is False
    assert _should_surface_windowed_fatal_error(frozen=False, stdout=None, stderr=None) is False


def test_local_service_manager_open_browser_uses_target_url() -> None:
    opened_urls: list[str] = []

    def fake_opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=Path(sys.executable),
        browser_opener=fake_opener,
    )

    assert manager.open_browser() is True
    assert opened_urls == [manager.web_url]


def test_local_service_manager_snapshot_stays_reuse_first_for_default_demo_entry() -> None:
    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=Path(sys.executable),
    )

    snapshot = manager.snapshot()

    assert snapshot.url == manager.experimental_demo_boot_url
    assert snapshot.url != manager.experimental_demo_fresh_boot_url


def test_local_service_manager_fresh_demo_url_stays_small_launcher_level_bypass() -> None:
    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=Path(sys.executable),
    )

    assert manager.experimental_demo_boot_url.endswith("?demo_boot=1")
    assert manager.experimental_demo_fresh_boot_url == (
        f"{manager.experimental_demo_boot_url}&fresh=1"
    )
    assert manager.experimental_demo_fresh_boot_url.startswith(manager.experimental_demo_url)


def test_local_service_manager_open_experimental_demo_uses_launcher_deep_link() -> None:
    opened_urls: list[str] = []

    def fake_opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=Path(sys.executable),
        browser_opener=fake_opener,
    )

    with patch("coc_runner.internal_local_launcher.probe_healthz", return_value=True):
        assert manager.open_experimental_demo() is True

    assert manager.experimental_demo_url.endswith(DEFAULT_EXPERIMENTAL_DEMO_PATH)
    assert opened_urls == [manager.experimental_demo_boot_url]


def test_local_service_manager_open_experimental_demo_fresh_uses_fresh_bypass_deep_link() -> None:
    opened_urls: list[str] = []

    def fake_opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=Path(sys.executable),
        browser_opener=fake_opener,
    )

    with patch("coc_runner.internal_local_launcher.probe_healthz", return_value=True):
        assert manager.open_experimental_demo(fresh=True) is True

    assert opened_urls == [manager.experimental_demo_fresh_boot_url]


def test_local_service_manager_open_experimental_demo_requires_running_service() -> None:
    opened_urls: list[str] = []

    def fake_opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=Path(sys.executable),
        browser_opener=fake_opener,
    )

    with patch("coc_runner.internal_local_launcher.probe_healthz", return_value=False):
        assert manager.open_experimental_demo() is False

    assert opened_urls == []
    assert "请先启动本地应用后再打开 experimental AI demo" in manager.snapshot().last_error


def test_launcher_cli_smoke_json_uses_real_entry() -> None:
    run_dir = _make_run_dir()
    port = _find_free_port()
    db_path = run_dir / "launcher-cli-smoke.db"
    env = os.environ.copy()
    env["COC_RUNNER_DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    try:
        result = subprocess.run(
            [
                str(Path(sys.executable)),
                "-m",
                "coc_runner.internal_local_launcher",
                "--smoke-json",
                "--project-root",
                str(PROJECT_ROOT),
                "--service-python",
                str(Path(sys.executable)),
                "--port",
                str(port),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr or result.stdout
        payload = json.loads(result.stdout)
        assert payload["mode"] == "smoke-json"
        assert payload["success"] is True
        assert payload["status_after_start"] == "running"
        assert payload["status_after_stop"] == "not_running"
        assert payload["browser_urls"] == [payload["experimental_demo_url"]]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_launcher_cli_smoke_json_can_write_output_file() -> None:
    run_dir = _make_run_dir()
    port = _find_free_port()
    db_path = run_dir / "launcher-cli-smoke-file.db"
    smoke_output = run_dir / "smoke-output.json"
    env = os.environ.copy()
    env["COC_RUNNER_DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    try:
        result = subprocess.run(
            [
                str(Path(sys.executable)),
                "-m",
                "coc_runner.internal_local_launcher",
                "--smoke-json",
                "--project-root",
                str(PROJECT_ROOT),
                "--service-python",
                str(Path(sys.executable)),
                "--port",
                str(port),
                "--smoke-output-file",
                str(smoke_output),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr or result.stdout
        assert smoke_output.exists() is True
        payload = json.loads(smoke_output.read_text(encoding="utf-8"))
        assert payload["success"] is True
        assert payload["browser_urls"] == [payload["experimental_demo_url"]]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_local_service_manager_start_failure_surfaces_error() -> None:
    run_dir = _make_run_dir()
    manager = LocalServiceManager(
        project_root=PROJECT_ROOT,
        service_python=run_dir / "missing-python.exe",
    )

    try:
        assert manager.start() is False
        snapshot = manager.snapshot()
        assert snapshot.status == "failed"
        assert "未找到项目虚拟环境解释器" in snapshot.last_error
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
