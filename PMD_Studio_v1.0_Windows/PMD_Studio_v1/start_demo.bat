@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
 set "PY=..\.venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
 set "PY=.venv\Scripts\python.exe"
) else (
 echo Run install.bat first.
 pause
 exit /b 1
)
set PMD_DEMO=1
echo DEMO MODE: Generated camera images, NOT real experiment data.
echo Interface : http://127.0.0.1:8000/studio
"%PY%" -m uvicorn studio.app:app --host 127.0.0.1 --port 8000 --workers 1
pause
