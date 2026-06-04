@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=python"

where python >nul 2>nul
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
  ) else (
    set "PYTHON_EXE=py"
  )
)

"%PYTHON_EXE%" -X utf8 "%SCRIPT_DIR%claude_statusline_capture.py"
