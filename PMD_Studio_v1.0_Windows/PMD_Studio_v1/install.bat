@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
  set "PY=..\.venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  py -3 -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python not found. Install Python 3.10+ or place studio inside PMD_P0_Starter.
    pause
    exit /b 1
  )
  set "PY=.venv\Scripts\python.exe"
)
echo Using Python: %PY%
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
 echo [ERROR] Dependency installation failed.
 pause
 exit /b 1
)
"%PY%" -c "import fastapi,cv2,scipy,numpy,matplotlib,uvicorn,multipart; print('Python dependencies OK')"
if errorlevel 1 (
 pause
 exit /b 1
)
echo.
echo Installed. Run start.bat (real camera) or start_demo.bat (UI demo).
pause
