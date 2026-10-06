@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SteamRentLibrarySoft

set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "REQ_FILE=requirements.txt"
set "REQ_STAMP=%VENV%\.requirements_stamp"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

echo.
echo ============================================================
echo   SteamRentLibrarySoft
echo ============================================================
echo.

if not exist "run.py" (
  echo [ERROR] run.py not found.
  goto :error
)
if not exist "%REQ_FILE%" (
  echo [ERROR] requirements.txt not found.
  goto :error
)

if exist "%PYTHON%" goto :venv_ready

echo [INFO] Creating virtual environment...
where py >nul 2>&1
if not errorlevel 1 (
  py -3.11 -m venv "%VENV%" >nul 2>&1
  if errorlevel 1 py -3 -m venv "%VENV%"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python 3.11+ was not found.
    echo Install Python and enable "Add Python to PATH".
    goto :error
  )
  python -m venv "%VENV%"
)
if errorlevel 1 goto :error
if not exist "%PYTHON%" goto :error
set "INSTALL_DEPS=1"
goto :dependencies

:venv_ready
set "INSTALL_DEPS=0"
for %%F in ("%REQ_FILE%") do set "CURRENT_STAMP=%%~zF_%%~tF"
set "OLD_STAMP="
if exist "%REQ_STAMP%" set /p OLD_STAMP=<"%REQ_STAMP%"
if not "!CURRENT_STAMP!"=="!OLD_STAMP!" set "INSTALL_DEPS=1"

:dependencies
if "%INSTALL_DEPS%"=="1" (
  echo [INFO] Installing/updating dependencies...
  "%PYTHON%" -m pip install --disable-pip-version-check --upgrade pip
  if errorlevel 1 goto :error
  "%PYTHON%" -m pip install --disable-pip-version-check -r "%REQ_FILE%"
  if errorlevel 1 goto :error
  for %%F in ("%REQ_FILE%") do set "CURRENT_STAMP=%%~zF_%%~tF"
  >"%REQ_STAMP%" echo !CURRENT_STAMP!
) else (
  echo [OK] Dependencies unchanged.
)

echo [INFO] Checking application imports...
"%PYTHON%" -c "import PySide6, sqlalchemy, requests, alembic; import app.main" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Import check failed. Reinstalling dependencies...
  "%PYTHON%" -m pip install --disable-pip-version-check -r "%REQ_FILE%"
  if errorlevel 1 goto :error
  "%PYTHON%" -c "import PySide6, sqlalchemy, requests, alembic; import app.main"
  if errorlevel 1 goto :error
)

echo [INFO] Starting SteamRentLibrarySoft...
"%PYTHON%" run.py
set "APP_EXIT=%ERRORLEVEL%"
if "%APP_EXIT%"=="0" (
  endlocal
  exit /b 0
)

echo.
echo [ERROR] Application exited with code %APP_EXIT%.
pause
endlocal
exit /b %APP_EXIT%

:error
echo.
echo [ERROR] Startup failed.
pause
endlocal
exit /b 1
