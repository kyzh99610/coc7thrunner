# CoC Runner MVP

Local-first backend MVP for a Call of Cthulhu 7th Edition tabletop assistant.

## Python Version

Python 3.12 is the required local development and runtime baseline for this project.

- Primary baseline: Python 3.12
- Secondary compatibility target: Python 3.13
- Do not build the project virtualenv from a Windows Store / `WindowsApps` Python shim

See [DEV_SETUP_WINDOWS.md](DEV_SETUP_WINDOWS.md) for the Windows-specific interpreter checks and migration smoke checklist.
If this machine already has a broken `.venv` that points at `WindowsApps`, use [WINDOWS_PY312_REPAIR.md](WINDOWS_PY312_REPAIR.md) and the explicit-path repair script instead of `py -3.12`.

## Fresh Setup

Use a real Python 3.12 install, create `.venv`, activate it, and then use `python -m ...` for all package installs and commands.

```powershell
py -0p
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
```

If `py -0p` only shows `WindowsApps` paths for Python 3.12, install Python 3.12 from python.org first and recreate `.venv` from that real interpreter. A virtualenv built from `WindowsApps` can leave `.venv\pyvenv.cfg` pointing at an unusable Store path.

## Windows Repair

If `.venv\pyvenv.cfg` points at `WindowsApps`, the shortest repair path is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair_windows_py312_env.ps1 -Python312Path "C:\Path\To\Python312\python.exe"
.\.venv\Scripts\Activate.ps1
python -m pytest -q
python -m uvicorn coc_runner.main:create_app --factory --reload
```

See [WINDOWS_PY312_REPAIR.md](WINDOWS_PY312_REPAIR.md) for the full explanation and explicit-path checks.

## Install Dependencies

Install runtime dependencies:

```powershell
python -m pip install -e .
```

Install development and test dependencies:

```powershell
python -m pip install -e .[dev]
```

The `dev` extra includes the test runner and FastAPI/Starlette test client dependency currently required by this repo:

- `pytest`
- `httpx`

## Run Tests

```powershell
python -m pytest -q
```

Do not rely on bare `pytest` on Windows. Use `python -m pytest` from the activated `.venv` so the command resolves against the project interpreter.

## Run The API

```powershell
python -m uvicorn coc_runner.main:create_app --factory --reload
```

Then open `http://127.0.0.1:8000/docs`.

Do not rely on bare `uvicorn` on Windows. Use `python -m uvicorn` from the activated `.venv` for the same interpreter-path reason.

## Current Scope

- FastAPI backend
- deterministic dice engine
- review-gated AI actions
- session rollback
- Chinese-first defaults
- text-first knowledge ingestion and retrieval

## Knowledge Ingest Notes

- Text and markdown rule ingestion are the primary supported paths.
- PDF ingest is currently shallow and text-stream based only.
- See [KNOWLEDGE_INGEST_LIMITATIONS.md](KNOWLEDGE_INGEST_LIMITATIONS.md) for current ingest limits.
