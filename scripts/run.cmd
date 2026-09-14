@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "SKETCH_ROOT=%~dp0.."
set "SKETCH_PY=%SKETCH_ROOT%\renderer\.venv\Scripts\python.exe"

if exist "%SKETCH_PY%" (
  "%SKETCH_PY%" "%SKETCH_ROOT%\renderer\scripts\prepare_env.py" --check >nul 2>nul
  if not errorlevel 1 goto :run
  "%SKETCH_PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "SKETCH_BOOTSTRAP_PY=%SKETCH_PY%"
    goto :bootstrap_executable
  )
)

if defined SKETCHNARRATOR_BOOTSTRAP_PYTHON (
  if exist "%SKETCHNARRATOR_BOOTSTRAP_PYTHON%" (
    "%SKETCHNARRATOR_BOOTSTRAP_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
      set "SKETCH_BOOTSTRAP_PY=%SKETCHNARRATOR_BOOTSTRAP_PYTHON%"
      goto :bootstrap_executable
    )
  )
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "SKETCH_PY_LAUNCHER=-3"
    goto :bootstrap_py_launcher
  )
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "SKETCH_BOOTSTRAP_PY=python"
    goto :bootstrap_executable
  )
)

set "SKETCH_CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%SKETCH_CODEX_PY%" (
  "%SKETCH_CODEX_PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "SKETCH_BOOTSTRAP_PY=%SKETCH_CODEX_PY%"
    goto :bootstrap_executable
  )
)

echo [err] Python 3.10+ was not found. The host can set SKETCHNARRATOR_BOOTSTRAP_PYTHON to a valid interpreter. 1>&2
exit /b 2

:bootstrap_py_launcher
if /I "%~1"=="doctor" (
  py %SKETCH_PY_LAUNCHER% -B "%SKETCH_ROOT%\scripts\doctor.py" %*
  exit /b
)
py %SKETCH_PY_LAUNCHER% "%SKETCH_ROOT%\renderer\scripts\prepare_env.py"
if errorlevel 1 exit /b %errorlevel%
goto :run

:bootstrap_executable
if /I "%~1"=="doctor" (
  "%SKETCH_BOOTSTRAP_PY%" -B "%SKETCH_ROOT%\scripts\doctor.py" %*
  exit /b
)
"%SKETCH_BOOTSTRAP_PY%" "%SKETCH_ROOT%\renderer\scripts\prepare_env.py"
if errorlevel 1 exit /b %errorlevel%

:run
if /I "%~1"=="doctor" (
  "%SKETCH_PY%" -B "%SKETCH_ROOT%\scripts\doctor.py" %*
  exit /b
)
"%SKETCH_PY%" "%SKETCH_ROOT%\scripts\workflow.py" %*
exit /b %errorlevel%
