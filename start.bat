@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required. See README.md for installation instructions.
  pause
  exit /b 1
)
uv sync --locked --no-dev
if errorlevel 1 (
  pause
  exit /b 1
)
uv run --frozen --no-sync shorthand-trainer %*
set "TRAINER_EXIT=%ERRORLEVEL%"
if not "%TRAINER_EXIT%"=="0" pause
exit /b %TRAINER_EXIT%
