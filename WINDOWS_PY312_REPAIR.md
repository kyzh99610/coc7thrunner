# Windows Python 3.12 Repair

This guide is for the case where the repo-local `.venv` was created from a Microsoft Store / `WindowsApps` Python 3.12 shim and is now unreliable on a Windows desktop machine.

## Why Turning Off The Alias May Still Not Fix It

Disabling the Windows App Execution Alias does not repair an existing virtual environment.

The important points are:

- `.venv\pyvenv.cfg` stores the base interpreter used when the virtual environment was created.
- If that file points at `WindowsApps`, the virtualenv is still tied to that old interpreter path even after alias settings change.
- The `py` launcher can still list Microsoft Store Python registrations, so `py -3.12` is not a reliable repair path on its own.
- PowerShell command resolution and old shell state can also make `python` look inconsistent across sessions.

If `.venv\pyvenv.cfg` points at `WindowsApps`, rebuild `.venv` from a real Python 3.12 executable path. Do not try to salvage the old virtualenv.

## Why A `WindowsApps`-Backed `.venv` Must Be Rebuilt

Check the current virtualenv metadata:

```powershell
Get-Content .\.venv\pyvenv.cfg
```

If `home =` or `executable =` points to a path like one of these, that `.venv` is not a reliable project environment:

- `C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\...`
- `C:\Program Files\WindowsApps\...`

An existing virtualenv does not rebind itself automatically. The only correct fix is:

1. Find a real Python 3.12 executable
2. Delete `.venv`
3. Recreate `.venv` from that explicit interpreter path
4. Reinstall project dependencies

## How To Confirm A Real Python 3.12 Is Installed

Do not assume `py -3.12` is usable.

Instead, find the actual `python.exe` path and validate it directly:

```powershell
& "C:\Path\To\Python312\python.exe" --version
& "C:\Path\To\Python312\python.exe" -c "import sys; print(sys.executable)"
```

What you want:

- `Python 3.12.x`
- A normal filesystem path to `python.exe`
- Not a `WindowsApps` location

Typical examples of a real install:

- `C:\Users\<you>\AppData\Local\Programs\Python\Python312\python.exe`
- `C:\Program Files\Python312\python.exe`
- This repo's bundled runtime if present: `D:\dev\coc7thrunner\.tools\python312\python.exe`

## Preferred Repair Command

Use the repo-local repair script with an explicit Python 3.12 path:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair_windows_py312_env.ps1 -Python312Path "C:\Path\To\Python312\python.exe"
```

This script will:

- verify the path exists
- verify it is really Python 3.12
- remove the current `.venv`
- create a new `.venv` from that exact interpreter
- run `python -m pip install --upgrade pip`
- run `python -m pip install -e .[dev]`
- print the new `python --version`
- print `sys.executable`
- print the new `.venv\pyvenv.cfg`

## Manual Explicit-Path Repair

If you do not want to use the script, the explicit-path repair flow is:

```powershell
Remove-Item -Recurse -Force .\.venv
& "C:\Path\To\Python312\python.exe" -m venv .\.venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
python -m pytest -q
python -m uvicorn coc_runner.main:create_app --factory --reload
```

Then open `http://127.0.0.1:8000/docs`.

## Shortest Repair Path On This Machine

1. Find a real Python 3.12 path.
2. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair_windows_py312_env.ps1 -Python312Path "C:\Path\To\Python312\python.exe"
```

3. Activate the repaired environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

4. Verify tests:

```powershell
python -m pytest -q
```

5. Start the API:

```powershell
python -m uvicorn coc_runner.main:create_app --factory --reload
```

6. Open `http://127.0.0.1:8000/docs`.
