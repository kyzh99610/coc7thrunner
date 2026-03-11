# Windows Dev Setup

## Goal

On a fresh Windows machine with Python 3.12, the supported path is:

1. Create and activate `.venv`
2. Install runtime dependencies
3. Install development and test dependencies
4. Run `python -m pytest -q`
5. Run `python -m uvicorn coc_runner.main:create_app --factory --reload`

Use `python -m ...` consistently after activation. Do not rely on bare `pip`, `pytest`, or `uvicorn`.

## Verify You Have A Real Python 3.12

Run these checks in PowerShell before creating `.venv`:

```powershell
py -0p
Get-Command python, py
```

What you want:

- A real Python 3.12 install that is not only a `WindowsApps` alias
- A launcher or interpreter path you can actually execute

What to avoid:

- `C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\...`
- `C:\Program Files\WindowsApps\...`

If Python 3.12 only appears under `WindowsApps`, install Python 3.12 from python.org and recreate `.venv` from that real interpreter. Do not build this repo's virtualenv from the Microsoft Store Python package.

## Create A Fresh Virtualenv

Recommended path when `py -3.12` resolves to a real interpreter:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
```

If you need to use an explicit interpreter path instead of `py`, use the real Python 3.12 executable directly:

```powershell
C:\Path\To\Python312\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
```

## Install Dependencies

Install runtime dependencies:

```powershell
python -m pip install -e .
```

Install development and test dependencies:

```powershell
python -m pip install -e .[dev]
```

`.[dev]` is the supported setup for local development and test runs. It includes:

- `pytest`
- `httpx` for the FastAPI / Starlette test client

## If `.venv` Was Built From `WindowsApps`

Check the virtualenv metadata:

```powershell
Get-Content .\.venv\pyvenv.cfg
```

If `home =` or `executable =` points to `WindowsApps`, delete `.venv` and recreate it from a real Python 3.12 install. A virtualenv built from `WindowsApps` can fail when you run `.venv\Scripts\python.exe` or any `python -m ...` command through that environment.

## Run Tests

```powershell
python -m pytest -q
```

## Run The API

```powershell
python -m uvicorn coc_runner.main:create_app --factory --reload
```

Then open `http://127.0.0.1:8000/docs`.

## Migration Smoke Checklist

- `python --version` reports `Python 3.12.x` from the activated `.venv`
- `python -m pytest -q` passes
- `python -m uvicorn coc_runner.main:create_app --factory --reload` starts successfully
- `http://127.0.0.1:8000/docs` opens successfully
