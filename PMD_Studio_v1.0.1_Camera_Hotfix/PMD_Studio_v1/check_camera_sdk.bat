@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
 set "PY=..\.venv\Scripts\python.exe"
 set "PMD_PROJECT_ROOT=%~dp0.."
) else if exist "..\..\.venv\Scripts\python.exe" (
 set "PY=..\..\.venv\Scripts\python.exe"
 set "PMD_PROJECT_ROOT=%~dp0..\.."
) else if exist ".venv\Scripts\python.exe" (
 set "PY=.venv\Scripts\python.exe"
 set "PMD_PROJECT_ROOT=%~dp0"
) else (
 echo ERROR: Python virtual environment not found.
 pause
 exit /b 1
)
set "PYTHONPATH=%PMD_PROJECT_ROOT%;%PYTHONPATH%"
echo Python: %PY%
"%PY%" check_camera_sdk.py
pause
