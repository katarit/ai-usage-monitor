@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"

set "PYTHON_CMD="
where python >nul 2>nul
if %ERRORLEVEL%==0 set "PYTHON_CMD=python"
if not defined PYTHON_CMD if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PYTHON_CMD=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PYTHON_CMD set "PYTHON_CMD=py"

set "CLAUDE_ONLINE_ARGS=--claude-online-usage --claude-online-ttl 300"
for %%A in (%*) do (
  if "%%~A"=="--no-claude-online-usage" set "CLAUDE_ONLINE_ARGS="
)
set "CODEX_CREDITS_ARGS=--codex-reset-credits --codex-reset-credits-ttl 300"
for %%A in (%*) do (
  if "%%~A"=="--no-codex-reset-credits" set "CODEX_CREDITS_ARGS="
)

if "%~1"=="" (
  "%PYTHON_CMD%" -m ai_usage_monitor --watch --profile normal %CLAUDE_ONLINE_ARGS% %CODEX_CREDITS_ARGS%
) else (
  "%PYTHON_CMD%" -m ai_usage_monitor %* %CLAUDE_ONLINE_ARGS% %CODEX_CREDITS_ARGS%
)
