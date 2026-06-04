@echo off
setlocal

cd /d "%~dp0\.."

echo AI Usage Monitor - Claude statusLine diagnostic
echo ==================================================
echo.

echo [1] User settings
if exist "%USERPROFILE%\.claude\settings.json" (
  type "%USERPROFILE%\.claude\settings.json"
) else (
  echo missing: %USERPROFILE%\.claude\settings.json
)
echo.
echo.

echo [2] Project local settings
if exist ".claude\settings.local.json" (
  type ".claude\settings.local.json"
) else (
  echo missing: .claude\settings.local.json
)
echo.
echo.

echo [3] Python capture script
if exist "scripts\claude_statusline_capture.py" (
  echo found: scripts\claude_statusline_capture.py
) else (
  echo missing: scripts\claude_statusline_capture.py
)
echo.
echo.

echo [4] Legacy cmd wrapper
if exist "scripts\claude_statusline_capture.cmd" (
  type "scripts\claude_statusline_capture.cmd"
) else (
  echo missing: scripts\claude_statusline_capture.cmd
)
echo.
echo.

echo [5] Expected command shape
echo C:/Users/tkro_/AppData/Local/Python/pythoncore-3.14-64/python.exe -X utf8 C:/Users/tkro_/Projects/tools-dev/ai-usage-monitor/scripts/claude_statusline_capture.py
echo.
echo Claude Code on Windows may parse statusLine.command bash-style, so use direct python.exe with forward-slash paths.
echo.
echo.

echo [6] Direct Python self-test with fixture input
if exist "tests\fixtures\claude-statusline-input.json" (
  set AI_USAGE_MONITOR_DIR=tests\_tmp
  type "tests\fixtures\claude-statusline-input.json" | "C:\Users\tkro_\AppData\Local\Python\pythoncore-3.14-64\python.exe" -X utf8 "scripts\claude_statusline_capture.py"
) else (
  echo missing: tests\fixtures\claude-statusline-input.json
)
echo.
echo.

echo [7] Self-test heartbeat
if exist "tests\_tmp\claude-statusline-heartbeat.json" (
  type "tests\_tmp\claude-statusline-heartbeat.json"
) else (
  echo missing: tests\_tmp\claude-statusline-heartbeat.json
)
echo.
echo.

echo [8] Real Claude heartbeat
if exist "%USERPROFILE%\.ai-usage-monitor\claude-statusline-heartbeat.json" (
  type "%USERPROFILE%\.ai-usage-monitor\claude-statusline-heartbeat.json"
) else (
  echo missing: %USERPROFILE%\.ai-usage-monitor\claude-statusline-heartbeat.json
  echo Claude Code has not executed the configured statusLine command yet.
)
echo.
echo.

echo [9] Real Claude capture
if exist "%USERPROFILE%\.ai-usage-monitor\claude-statusline.json" (
  type "%USERPROFILE%\.ai-usage-monitor\claude-statusline.json"
) else (
  echo missing: %USERPROFILE%\.ai-usage-monitor\claude-statusline.json
)
echo.
