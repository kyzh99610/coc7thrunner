# Python Version Notes

## Canonical Baseline

- Required local development/runtime version: Python 3.12
- Secondary compatibility target: Python 3.13

## Current Local State

This workspace now has a usable project-local Python 3.12 interpreter and virtual environment.

Observed local state:

- `python.exe` resolves to `C:\Users\12455\AppData\Local\Microsoft\WindowsApps\python.exe`
- `py.exe` resolves to `C:\Users\12455\AppData\Local\Microsoft\WindowsApps\py.exe`
- `C:\Users\12455\AppData\Local\Microsoft\WindowsApps\python.exe` is a zero-byte Windows Store alias, not a real interpreter
- Project-local Python 3.12 is installed at `C:\Users\12455\OneDrive\Desktop\coc7thrunner\.tools\python312\python.exe`
- The repo-local virtual environment is at `C:\Users\12455\OneDrive\Desktop\coc7thrunner\.venv`
- The helper script `scripts\Use-ProjectPython312.ps1` prepends the repo runtime and venv to `PATH` and sets `TMP`/`TEMP` to the project-local `.tmp` directory

Impact:

- The repo now has a working Python 3.12 runtime for local development and test execution
- Bare `python` and `py` are still not trustworthy until the helper script is loaded or the explicit `.venv\Scripts\python.exe` path is used
- System-wide PATH is still not standardized; the project is standardized through its local runtime and virtual environment

## Remaining Work To Fully Drop 3.10-Era Fallbacks

The runtime fallback has been removed from `src/coc_runner/compat.py`. The codebase now assumes Python 3.12+ directly.

Remaining environment cleanup:

1. Add CI coverage for Python 3.12 as required and Python 3.13 as compatibility validation.
2. Optionally install a real system-wide Python 3.12 and register `py -3.12` if you want machine-level defaults to match the repo defaults.
3. If needed, replace the manual pip seeding workaround with a cleaner bootstrap step once the local temp-permission issue is understood.
