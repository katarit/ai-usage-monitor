@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
if "%~1"=="" (
  python -m ai_usage_monitor --watch --refresh 15
) else (
  python -m ai_usage_monitor %*
)
