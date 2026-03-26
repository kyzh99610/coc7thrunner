from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_WEB_PATH = "/app/sessions"
DEFAULT_EXPERIMENTAL_DEMO_PATH = "/app/experimental-ai-demo"
DEFAULT_EXPERIMENTAL_DEMO_BOOT_QUERY = "?demo_boot=1"
DEFAULT_HEALTH_PATH = "/healthz"
MAX_LOG_LINES = 200
ENV_PROBE_TIMEOUT_SECONDS = 15.0
HEALTHCHECK_TIMEOUT_SECONDS = 0.5
STARTUP_TIMEOUT_SECONDS = 15.0
PROJECT_ROOT_SENTINEL = Path("src") / "coc_runner" / "main.py"
FATAL_ERROR_LOG_NAME = "internal-local-launcher-fatal.log"
FATAL_ERROR_TITLE = "CoC Runner Internal Local Launcher"
_UNSET = object()

_PYTHON_INFO_SCRIPT = """
import json
import sys

print(json.dumps({
    "python_version": sys.version.split()[0],
    "python_executable": sys.executable,
}, ensure_ascii=False))
""".strip()

_APP_PROBE_SCRIPT = """
import json
import uvicorn

from coc_runner.config import get_settings
from coc_runner.main import create_app

settings = get_settings()
create_app(settings)

print(json.dumps({
    "app_entry": "coc_runner.main:create_app",
    "app_importable": True,
    "uvicorn_available": True,
    "local_llm_enabled": settings.local_llm_enabled,
    "local_llm_base_url": settings.local_llm_base_url,
    "local_llm_model": settings.local_llm_model,
    "dice_backend_mode": settings.dice_backend_mode,
}, ensure_ascii=False))
""".strip()


@dataclass(slots=True)
class EnvironmentSnapshot:
    project_root: Path
    service_python: Path
    bundled_python: Path
    python_available: bool = False
    python_version: str = ""
    python_executable: str = ""
    app_entry: str = "coc_runner.main:create_app"
    app_importable: bool = False
    uvicorn_available: bool = False
    local_llm_enabled: bool = False
    local_llm_base_url: str = ""
    local_llm_model: str = ""
    dice_backend_mode: str = ""
    error: str = ""

    @property
    def llm_summary(self) -> str:
        if not self.local_llm_enabled:
            return "未启用"
        base_url = self.local_llm_base_url or "未配置"
        model = self.local_llm_model or "未配置"
        return f"已启用 | base_url={base_url} | model={model}"


@dataclass(slots=True)
class LauncherSnapshot:
    status: str
    status_text: str
    url: str
    port: int
    last_error: str
    owns_process: bool
    logs: list[str] = field(default_factory=list)
    environment: EnvironmentSnapshot | None = None


def project_root_from_module() -> Path:
    return resolve_project_root()


def _looks_like_project_root(candidate: Path) -> bool:
    return (candidate / "pyproject.toml").exists() and (candidate / PROJECT_ROOT_SENTINEL).exists()


def resolve_project_root(
    *,
    env_project_root: str | None = None,
    executable_path: Path | None = None,
    module_path: Path | None = None,
    cwd: Path | None = None,
    frozen: bool | None = None,
) -> Path:
    resolved_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    runtime_executable = (executable_path or Path(sys.executable)).resolve()
    runtime_module = (module_path or Path(__file__)).resolve()
    runtime_cwd = (cwd or Path.cwd()).resolve()
    candidates: list[Path] = []
    if env_project_root:
        candidates.append(Path(env_project_root).resolve())
    if resolved_frozen:
        executable_dir = runtime_executable.parent
        candidates.append(executable_dir)
        candidates.extend(parent for parent in executable_dir.parents)
    if len(runtime_module.parents) >= 3:
        candidates.append(runtime_module.parents[2])
    candidates.append(runtime_cwd)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _looks_like_project_root(candidate):
            return candidate
    if len(runtime_module.parents) >= 3:
        return runtime_module.parents[2]
    return runtime_cwd


def default_service_python(project_root: Path) -> Path:
    return project_root / ".venv" / "Scripts" / "python.exe"


def default_launcher_pythonw(project_root: Path) -> Path:
    return project_root / ".venv" / "Scripts" / "pythonw.exe"


def bundled_python(project_root: Path) -> Path:
    return project_root / ".tools" / "python312" / "python.exe"


def build_launcher_subprocess_env(
    project_root: Path,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    src_path = str(project_root / "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    path_parts = [src_path]
    if existing_pythonpath:
        path_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(path_parts)
    temp_root = project_root / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    return env


def build_service_command(
    *,
    service_python: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> list[str]:
    return [
        str(service_python),
        "-m",
        "uvicorn",
        "coc_runner.main:create_app",
        "--factory",
        "--host",
        host,
        "--port",
        str(port),
    ]


def _run_json_probe(
    *,
    service_python: Path,
    script: str,
    project_root: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
) -> tuple[dict[str, object] | None, str]:
    try:
        result = subprocess.run(
            [str(service_python), "-c", script],
            cwd=project_root,
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        return None, str(exc)
    except subprocess.TimeoutExpired:
        return None, f"环境探测超时（>{timeout_seconds:.0f}s）"
    combined_error = _tail_error_text(result.stderr, result.stdout)
    if result.returncode != 0:
        return None, combined_error or f"环境探测失败，退出码 {result.returncode}"
    stdout_text = result.stdout.strip()
    if not stdout_text:
        return None, "环境探测没有返回任何输出"
    try:
        return json.loads(stdout_text), ""
    except json.JSONDecodeError:
        return None, f"环境探测返回了非 JSON 输出：{stdout_text}"


def collect_environment_snapshot(
    *,
    project_root: Path,
    env: Mapping[str, str] | None = None,
    service_python: Path | None = None,
) -> EnvironmentSnapshot:
    resolved_project_root = project_root.resolve()
    resolved_service_python = (service_python or default_service_python(resolved_project_root)).resolve()
    snapshot = EnvironmentSnapshot(
        project_root=resolved_project_root,
        service_python=resolved_service_python,
        bundled_python=bundled_python(resolved_project_root),
    )
    if not resolved_service_python.exists():
        snapshot.error = f"未找到项目虚拟环境解释器：{resolved_service_python}"
        return snapshot
    probe_env = build_launcher_subprocess_env(resolved_project_root, env)
    python_info, python_error = _run_json_probe(
        service_python=resolved_service_python,
        script=_PYTHON_INFO_SCRIPT,
        project_root=resolved_project_root,
        env=probe_env,
        timeout_seconds=ENV_PROBE_TIMEOUT_SECONDS,
    )
    if python_info is None:
        snapshot.error = python_error
        return snapshot
    snapshot.python_available = True
    snapshot.python_version = str(python_info.get("python_version") or "")
    snapshot.python_executable = str(python_info.get("python_executable") or "")
    app_info, app_error = _run_json_probe(
        service_python=resolved_service_python,
        script=_APP_PROBE_SCRIPT,
        project_root=resolved_project_root,
        env=probe_env,
        timeout_seconds=ENV_PROBE_TIMEOUT_SECONDS,
    )
    if app_info is None:
        snapshot.error = app_error
        return snapshot
    snapshot.app_entry = str(app_info.get("app_entry") or snapshot.app_entry)
    snapshot.app_importable = bool(app_info.get("app_importable"))
    snapshot.uvicorn_available = bool(app_info.get("uvicorn_available"))
    snapshot.local_llm_enabled = bool(app_info.get("local_llm_enabled"))
    snapshot.local_llm_base_url = str(app_info.get("local_llm_base_url") or "")
    snapshot.local_llm_model = str(app_info.get("local_llm_model") or "")
    snapshot.dice_backend_mode = str(app_info.get("dice_backend_mode") or "")
    return snapshot


def probe_healthz(health_url: str, *, timeout_seconds: float = HEALTHCHECK_TIMEOUT_SECONDS) -> bool:
    try:
        with urllib.request.urlopen(health_url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("status") == "ok"
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
        OSError,
    ):
        return False


def _tail_error_text(stderr_text: str, stdout_text: str, *, line_limit: int = 8) -> str:
    combined = "\n".join(
        line.strip()
        for line in (stderr_text.splitlines() + stdout_text.splitlines())
        if line.strip()
    )
    if not combined:
        return ""
    lines = combined.splitlines()
    return "\n".join(lines[-line_limit:])


def _find_free_port(host: str = DEFAULT_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _environment_snapshot_payload(snapshot: EnvironmentSnapshot | None) -> dict[str, object]:
    if snapshot is None:
        return {}
    return {
        "project_root": str(snapshot.project_root),
        "service_python": str(snapshot.service_python),
        "bundled_python": str(snapshot.bundled_python),
        "python_available": snapshot.python_available,
        "python_version": snapshot.python_version,
        "python_executable": snapshot.python_executable,
        "app_entry": snapshot.app_entry,
        "app_importable": snapshot.app_importable,
        "uvicorn_available": snapshot.uvicorn_available,
        "local_llm_enabled": snapshot.local_llm_enabled,
        "local_llm_base_url": snapshot.local_llm_base_url,
        "local_llm_model": snapshot.local_llm_model,
        "dice_backend_mode": snapshot.dice_backend_mode,
        "error": snapshot.error,
    }


def _fatal_error_log_path(project_root: Path | None = None) -> Path:
    resolved_project_root = (project_root or project_root_from_module()).resolve()
    temp_root = resolved_project_root / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root / FATAL_ERROR_LOG_NAME


def _should_surface_windowed_fatal_error(
    *,
    frozen: bool | None = None,
    stdout: object = _UNSET,
    stderr: object = _UNSET,
) -> bool:
    resolved_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    resolved_stdout = sys.stdout if stdout is _UNSET else stdout
    resolved_stderr = sys.stderr if stderr is _UNSET else stderr
    return resolved_frozen and (resolved_stdout is None or resolved_stderr is None)


def _show_native_error_dialog(title: str, message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        return


def report_fatal_launcher_error(
    exc: BaseException,
    *,
    project_root: Path | None = None,
    reporter: Callable[[str, str], None] | None = None,
) -> Path:
    log_path = _fatal_error_log_path(project_root)
    traceback_text = "".join(traceback.format_exception(exc))
    log_path.write_text(traceback_text, encoding="utf-8")
    summary = (
        "launcher 启动失败。\n\n"
        f"{exc}\n\n"
        f"详细日志：{log_path}"
    )
    (reporter or _show_native_error_dialog)(FATAL_ERROR_TITLE, summary)
    return log_path


def _emit_smoke_payload(
    payload: Mapping[str, object],
    *,
    output_path: Path | None = None,
) -> None:
    payload_text = json.dumps(payload, ensure_ascii=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload_text, encoding="utf-8")
    if sys.stdout is not None:
        print(payload_text)


class LocalServiceManager:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        service_python: Path | None = None,
        base_env: Mapping[str, str] | None = None,
        browser_opener: Callable[[str], bool] | None = None,
    ) -> None:
        self.project_root = (project_root or project_root_from_module()).resolve()
        self.host = host
        self.port = port
        self.service_python = (service_python or default_service_python(self.project_root)).resolve()
        self.base_env = dict(base_env or os.environ)
        self.browser_opener = browser_opener or webbrowser.open
        self._process: subprocess.Popen[str] | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._log_lines: list[str] = []
        self._status = "not_running"
        self._status_text = "未启动"
        self._last_error = ""
        self._environment_snapshot: EnvironmentSnapshot | None = None
        self.refresh_environment()
        if probe_healthz(self.health_url):
            self._status = "running_external"
            self._status_text = "已运行（外部）"

    @property
    def web_url(self) -> str:
        return f"http://{self.host}:{self.port}{DEFAULT_WEB_PATH}"

    @property
    def experimental_demo_url(self) -> str:
        return f"http://{self.host}:{self.port}{DEFAULT_EXPERIMENTAL_DEMO_PATH}"

    @property
    def experimental_demo_boot_url(self) -> str:
        return f"{self.experimental_demo_url}{DEFAULT_EXPERIMENTAL_DEMO_BOOT_QUERY}"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}{DEFAULT_HEALTH_PATH}"

    def snapshot(self) -> LauncherSnapshot:
        return LauncherSnapshot(
            status=self._status,
            status_text=self._status_text,
            url=self.experimental_demo_boot_url,
            port=self.port,
            last_error=self._last_error,
            owns_process=self._process is not None,
            logs=list(self._log_lines),
            environment=self._environment_snapshot,
        )

    def refresh_environment(self) -> EnvironmentSnapshot:
        self._environment_snapshot = collect_environment_snapshot(
            project_root=self.project_root,
            env=self.base_env,
            service_python=self.service_python,
        )
        return self._environment_snapshot

    def start(self) -> bool:
        self.poll()
        if self._process is not None:
            return False
        if probe_healthz(self.health_url):
            self._status = "running_external"
            self._status_text = "已运行（外部）"
            self._last_error = "检测到已有服务占用当前 URL；此窗口不会重复启动第二个实例。"
            return False
        snapshot = self.refresh_environment()
        if not snapshot.python_available or not snapshot.app_importable or not snapshot.uvicorn_available:
            self._status = "failed"
            self._status_text = "启动失败"
            self._last_error = snapshot.error or "当前环境未通过基础检查。"
            return False
        self._last_error = ""
        self._status = "starting"
        self._status_text = "启动中"
        self._log_lines.clear()
        self._drain_log_queue()
        command = build_service_command(
            service_python=self.service_python,
            host=self.host,
            port=self.port,
        )
        try:
            self._process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=build_launcher_subprocess_env(self.project_root, self.base_env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self._status = "failed"
            self._status_text = "启动失败"
            self._last_error = str(exc)
            self._process = None
            return False
        self._start_log_thread()
        return True

    def stop(self) -> bool:
        self.poll()
        if self._process is None:
            if probe_healthz(self.health_url):
                self._status = "running_external"
                self._status_text = "已运行（外部）"
                self._last_error = "当前服务不是由 launcher 启动，无法从此窗口停止。"
            else:
                self._status = "not_running"
                self._status_text = "未启动"
            return False
        self._status = "stopping"
        self._status_text = "停止中"
        process = self._process
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self._process = None
        self._drain_log_queue()
        if probe_healthz(self.health_url):
            self._status = "failed"
            self._status_text = "停止失败"
            self._last_error = "服务进程已退出，但健康检查仍然存活。"
            return False
        self._status = "not_running"
        self._status_text = "未启动"
        self._last_error = ""
        return True

    def open_browser(self) -> bool:
        return self._open_url(self.web_url)

    def open_experimental_demo(self) -> bool:
        if not probe_healthz(self.health_url):
            self.poll()
            self._last_error = "当前服务未运行；请先启动本地应用后再打开 experimental AI demo。"
            return False
        return self._open_url(self.experimental_demo_boot_url)

    def _open_url(self, url: str) -> bool:
        try:
            return bool(self.browser_opener(url))
        except Exception as exc:  # pragma: no cover - browser failures depend on host integration
            self._last_error = str(exc)
            return False

    def wait_until_running(self, *, timeout_seconds: float = STARTUP_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.poll().status in {"running", "running_external"}:
                return True
            if self._status == "failed":
                return False
            time.sleep(0.1)
        self._status = "failed"
        self._status_text = "启动失败"
        self._last_error = self._last_error or "等待 /healthz 就绪超时。"
        return False

    def poll(self) -> LauncherSnapshot:
        self._drain_log_queue()
        process = self._process
        if process is None:
            if probe_healthz(self.health_url):
                self._status = "running_external"
                self._status_text = "已运行（外部）"
            elif self._status != "failed":
                self._status = "not_running"
                self._status_text = "未启动"
            return self.snapshot()
        return_code = process.poll()
        if return_code is None:
            if probe_healthz(self.health_url):
                self._status = "running"
                self._status_text = "已运行"
            else:
                self._status = "starting"
                self._status_text = "启动中"
            return self.snapshot()
        self._process = None
        self._drain_log_queue()
        if self._status == "stopping":
            self._status = "not_running"
            self._status_text = "未启动"
            self._last_error = ""
            return self.snapshot()
        self._status = "failed"
        self._status_text = "启动失败"
        self._last_error = self._last_error or _tail_error_text("", "\n".join(self._log_lines))
        if not self._last_error:
            self._last_error = f"服务进程异常退出，退出码 {return_code}"
        return self.snapshot()

    def _start_log_thread(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        def _reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                cleaned = line.rstrip()
                if cleaned:
                    self._log_queue.put(cleaned)

        thread = threading.Thread(target=_reader, name="coc-runner-launcher-log-reader", daemon=True)
        thread.start()

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self._log_lines.append(line)
        if len(self._log_lines) > MAX_LOG_LINES:
            self._log_lines = self._log_lines[-MAX_LOG_LINES:]


def run_headless_smoke(
    *,
    project_root: Path | None = None,
    service_python: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    base_env: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    selected_port = port if port > 0 else _find_free_port(host)
    opened_urls: list[str] = []

    def _capture_browser(url: str) -> bool:
        opened_urls.append(url)
        return True

    manager = LocalServiceManager(
        project_root=project_root,
        host=host,
        port=selected_port,
        service_python=service_python,
        base_env=base_env,
        browser_opener=_capture_browser,
    )
    initial_snapshot = manager.snapshot()
    payload: dict[str, object] = {
        "mode": "smoke-json",
        "project_root": str(manager.project_root),
        "service_python": str(manager.service_python),
        "host": manager.host,
        "port": manager.port,
        "web_url": manager.web_url,
        "experimental_demo_url": manager.experimental_demo_boot_url,
        "health_url": manager.health_url,
        "status_before": initial_snapshot.status,
        "status_before_text": initial_snapshot.status_text,
        "environment": _environment_snapshot_payload(initial_snapshot.environment),
        "start_called": False,
        "wait_until_running": False,
        "browser_opened": False,
        "browser_urls": [],
        "stop_called": False,
        "status_after_start": initial_snapshot.status,
        "status_after_start_text": initial_snapshot.status_text,
        "status_after_stop": initial_snapshot.status,
        "status_after_stop_text": initial_snapshot.status_text,
        "last_error": initial_snapshot.last_error,
    }
    try:
        start_called = manager.start()
        payload["start_called"] = start_called
        wait_result = manager.wait_until_running(timeout_seconds=STARTUP_TIMEOUT_SECONDS) if start_called else False
        payload["wait_until_running"] = wait_result
        running_snapshot = manager.poll()
        payload["status_after_start"] = running_snapshot.status
        payload["status_after_start_text"] = running_snapshot.status_text
        payload["last_error"] = running_snapshot.last_error
        if running_snapshot.status == "running":
            payload["browser_opened"] = manager.open_experimental_demo()
            payload["browser_urls"] = list(opened_urls)
        if manager.snapshot().owns_process:
            payload["stop_called"] = manager.stop()
        final_snapshot = manager.poll()
        payload["status_after_stop"] = final_snapshot.status
        payload["status_after_stop_text"] = final_snapshot.status_text
        payload["last_error"] = final_snapshot.last_error or str(payload["last_error"])
        success = bool(
            payload["start_called"]
            and payload["wait_until_running"]
            and payload["browser_opened"]
            and payload["stop_called"]
            and final_snapshot.status == "not_running"
        )
        payload["success"] = success
        return (0 if success else 1), payload
    finally:
        if manager.snapshot().owns_process:
            manager.stop()


def _environment_display_text(snapshot: EnvironmentSnapshot | None) -> str:
    if snapshot is None:
        return "环境检查尚未执行。"
    lines = [
        f"项目目录：{snapshot.project_root}",
        f"服务解释器：{snapshot.service_python}",
        f"Python：{'可用' if snapshot.python_available else '不可用'}"
        + (f" | version={snapshot.python_version}" if snapshot.python_version else ""),
        f"ASGI app：{'可导入' if snapshot.app_importable else '不可导入'} | entry={snapshot.app_entry}",
        f"uvicorn：{'可用' if snapshot.uvicorn_available else '不可用'}",
        f"local LLM：{snapshot.llm_summary}",
    ]
    if snapshot.dice_backend_mode:
        lines.append(f"dice backend：{snapshot.dice_backend_mode}")
    if snapshot.error:
        lines.append(f"错误：{snapshot.error}")
    return "\n".join(lines)


def run_launcher_window(
    *,
    project_root: Path | None = None,
    service_python: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    try:
        import tkinter as tk
        from tkinter import scrolledtext, ttk
    except ImportError as exc:  # pragma: no cover - depends on interpreter build
        raise RuntimeError("当前 Python 缺少 Tkinter，无法启动 internal local launcher。") from exc

    manager = LocalServiceManager(
        project_root=project_root,
        service_python=service_python,
        host=host,
        port=port,
    )
    root = tk.Tk()
    root.title("CoC Runner Internal Local Launcher")
    root.geometry("760x620")
    root.minsize(680, 520)

    status_var = tk.StringVar(value=manager.snapshot().status_text)
    url_var = tk.StringVar(value=manager.experimental_demo_boot_url)
    port_var = tk.StringVar(value=str(manager.port))
    error_var = tk.StringVar(value="")
    env_var = tk.StringVar(value=_environment_display_text(manager.snapshot().environment))

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    header = ttk.Frame(frame)
    header.pack(fill="x")
    ttk.Label(
        header,
        text="CoC Runner Internal Local Launcher",
        font=("Segoe UI", 14, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text="仅供本地 / internal 使用：一键启动本地服务并打开 experimental observer demo，也可手动回到 Sessions 首页做 very small 环境检查。",
    ).pack(anchor="w", pady=(4, 10))

    controls = ttk.Frame(frame)
    controls.pack(fill="x", pady=(0, 10))
    ttk.Button(controls, text="检查环境", command=lambda: _refresh_environment()).pack(side="left")
    ttk.Button(controls, text="启动并打开 Observer Demo", command=lambda: _start_service()).pack(side="left", padx=(8, 0))
    ttk.Button(controls, text="停止本地应用", command=lambda: _stop_service()).pack(side="left", padx=(8, 0))
    ttk.Button(controls, text="打开 Sessions 首页", command=lambda: _open_browser()).pack(side="left", padx=(8, 0))
    ttk.Button(
        controls,
        text="重新打开 Observer Demo",
        command=lambda: _open_experimental_demo(),
    ).pack(side="left", padx=(8, 0))

    status_frame = ttk.LabelFrame(frame, text="服务状态", padding=10)
    status_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(status_frame, textvariable=status_var).pack(anchor="w")
    ttk.Label(status_frame, text="URL：").pack(anchor="w")
    ttk.Label(status_frame, textvariable=url_var).pack(anchor="w", padx=(18, 0))
    ttk.Label(status_frame, text="端口：").pack(anchor="w", pady=(6, 0))
    ttk.Label(status_frame, textvariable=port_var).pack(anchor="w", padx=(18, 0))
    ttk.Label(
        status_frame,
        textvariable=error_var,
        foreground="#a61c00",
        justify="left",
        wraplength=700,
    ).pack(anchor="w", pady=(8, 0))

    env_frame = ttk.LabelFrame(frame, text="环境检查", padding=10)
    env_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(
        env_frame,
        textvariable=env_var,
        justify="left",
        wraplength=700,
    ).pack(anchor="w")

    log_frame = ttk.LabelFrame(frame, text="日志 / 错误摘要", padding=10)
    log_frame.pack(fill="both", expand=True)
    log_box = scrolledtext.ScrolledText(log_frame, height=16, wrap="word", state="disabled")
    log_box.pack(fill="both", expand=True)

    def _render_logs(lines: list[str]) -> None:
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.insert("end", "\n".join(lines) if lines else "当前还没有日志输出。")
        log_box.see("end")
        log_box.configure(state="disabled")

    def _apply_snapshot(snapshot: LauncherSnapshot) -> None:
        status_var.set(snapshot.status_text)
        url_var.set(snapshot.url)
        port_var.set(str(snapshot.port))
        error_var.set(snapshot.last_error)
        env_var.set(_environment_display_text(snapshot.environment))
        _render_logs(snapshot.logs)

    def _refresh_environment() -> None:
        snapshot = manager.refresh_environment()
        _apply_snapshot(manager.snapshot())
        if snapshot.error and manager.snapshot().status not in {"running", "running_external"}:
            status_var.set("环境未就绪")

    def _start_service() -> None:
        started = manager.start()
        if started:
            manager.wait_until_running(timeout_seconds=STARTUP_TIMEOUT_SECONDS)
        snapshot = manager.poll()
        if snapshot.status in {"running", "running_external"}:
            manager.open_experimental_demo()
        _apply_snapshot(manager.snapshot())

    def _stop_service() -> None:
        manager.stop()
        _apply_snapshot(manager.snapshot())

    def _open_browser() -> None:
        manager.open_browser()
        _apply_snapshot(manager.snapshot())

    def _open_experimental_demo() -> None:
        manager.open_experimental_demo()
        _apply_snapshot(manager.snapshot())

    def _poll() -> None:
        _apply_snapshot(manager.poll())
        root.after(300, _poll)

    def _close() -> None:
        if manager.snapshot().owns_process:
            manager.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _close)
    _apply_snapshot(manager.snapshot())
    _poll()
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CoC Runner internal local launcher",
    )
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--service-python", type=Path, default=None)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--smoke-json",
        action="store_true",
        help="Run the internal exe smoke path without opening the GUI window.",
    )
    parser.add_argument(
        "--smoke-output-file",
        type=Path,
        default=None,
        help="Write the smoke-json payload to a file for windowed exe validation.",
    )
    args = parser.parse_args(argv)
    try:
        if args.smoke_json:
            exit_code, payload = run_headless_smoke(
                project_root=args.project_root,
                service_python=args.service_python,
                host=args.host,
                port=args.port,
                base_env=os.environ,
            )
            _emit_smoke_payload(payload, output_path=args.smoke_output_file)
            return exit_code
        run_launcher_window(
            project_root=args.project_root,
            service_python=args.service_python,
            host=args.host,
            port=args.port,
        )
        return 0
    except Exception as exc:
        if args.smoke_json:
            log_path = report_fatal_launcher_error(
                exc,
                project_root=args.project_root,
                reporter=lambda _title, _message: None,
            )
            payload = {
                "mode": "smoke-json",
                "success": False,
                "last_error": str(exc),
                "fatal_error_log": str(log_path),
            }
            _emit_smoke_payload(payload, output_path=args.smoke_output_file)
            return 1
        if _should_surface_windowed_fatal_error():
            report_fatal_launcher_error(exc, project_root=args.project_root)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
