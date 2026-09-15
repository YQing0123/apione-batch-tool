@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "RUNTIME=%SCRIPT_DIR%runtime\windows-x64\python.exe"
if not exist "%RUNTIME%" set "RUNTIME=python"
where "%RUNTIME%" >nul 2>&1
if errorlevel 1 (
  echo 未找到内置 Python 运行时：%SCRIPT_DIR%runtime\windows-x64\python.exe
  pause
  exit /b 1
)
cd /d "%SCRIPT_DIR%"
"%RUNTIME%" "%SCRIPT_DIR%apione_batch_gui.py"
endlocal
