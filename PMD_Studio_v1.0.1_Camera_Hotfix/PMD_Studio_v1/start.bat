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
 echo Python environment not found. Run install.bat first.
 pause
 exit /b 1
)
rem Include the old PMD project root so its local gxipy package can be found.
set "PYTHONPATH=%PMD_PROJECT_ROOT%;%PYTHONPATH%"
set PMD_DEMO=0
echo.
echo PC interface : http://127.0.0.1:8000/studio
echo iPad pattern : http://YOUR_PC_LAN_IP:8000/
echo Data folder  : %~dp0data
 echo Press Ctrl+C to stop. Do NOT run old serve_ipad.py on port 8000 concurrently.
"%PY%" -m uvicorn studio.app:app --host 0.0.0.0 --port 8000 --workers 1
if errorlevel 1 (
 echo [ERROR] Failed to start. Check if port 8000 is used.
 pause
)
