@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHONPATH=%PROJECT_ROOT%\src"
set "LAUNCHER_MODULE=coc_runner.internal_local_launcher"
set "PYTHON_EXE="

if exist "%PROJECT_ROOT%\.venv\Scripts\pythonw.exe" set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\pythonw.exe"
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\.tools\python312\pythonw.exe" set "PYTHON_EXE=%PROJECT_ROOT%\.tools\python312\pythonw.exe"
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\.tools\python312\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\.tools\python312\python.exe"

if not defined PYTHON_EXE (
  echo Could not find a usable Python runtime for the internal local launcher.
  echo Expected one of:
  echo   %PROJECT_ROOT%\.venv\Scripts\pythonw.exe
  echo   %PROJECT_ROOT%\.tools\python312\pythonw.exe
  pause
  exit /b 1
)

pushd "%PROJECT_ROOT%"
"%PYTHON_EXE%" -m %LAUNCHER_MODULE%
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
  echo Launcher exited with code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%
