from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path
from uuid import uuid4

from coc_runner.internal_local_launcher import (
    DEFAULT_HOST,
    LocalServiceManager,
    build_service_command,
    collect_environment_snapshot,
    probe_healthz,
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
