@echo off
REM ============================================================
REM Install ACDB Customer API as a Windows Service using NSSM
REM ============================================================
REM
REM Prerequisites:
REM   - Run setup.bat first to create venv and install deps
REM   - Download NSSM from https://nssm.cc/download
REM   - Place nssm.exe in this directory or add to PATH
REM
REM This creates a Windows service that auto-starts on boot.
REM ============================================================

echo.
echo ============================================================
echo  Installing ACDB Customer API as Windows Service
echo ============================================================
echo.

set SERVICE_NAME=ACDBCustomerAPI
set SCRIPT_DIR=%~dp0

REM Check NSSM
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%SCRIPT_DIR%nssm.exe" (
        set NSSM=%SCRIPT_DIR%nssm.exe
    ) else (
        echo ERROR: nssm.exe not found. Download from https://nssm.cc/download
        echo Place nssm.exe in this directory and run again.
        pause
        exit /b 1
    )
) else (
    set NSSM=nssm
)

REM Remove existing service if present
%NSSM% stop %SERVICE_NAME% >nul 2>&1
%NSSM% remove %SERVICE_NAME% confirm >nul 2>&1

REM Install service
echo Installing service: %SERVICE_NAME%
%NSSM% install %SERVICE_NAME% "%SCRIPT_DIR%venv\Scripts\python.exe" "%SCRIPT_DIR%customer_api.py"
%NSSM% set %SERVICE_NAME% AppDirectory "%SCRIPT_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "ACDB Customer Lookup API"
%NSSM% set %SERVICE_NAME% Description "FastAPI service for querying the 1PWR Access Customer Database"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppStdout "%SCRIPT_DIR%logs\stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%SCRIPT_DIR%logs\stderr.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 5242880

REM Create logs dir
if not exist "%SCRIPT_DIR%logs" mkdir "%SCRIPT_DIR%logs"

REM Set environment
if not defined ACDB_PATH (
    set ACDB_PATH=C:\Users\Administrator\Desktop\AccessDB_Clone\tuacc.accdb
)
%NSSM% set %SERVICE_NAME% AppEnvironmentExtra ACDB_PATH=%ACDB_PATH%

REM Start
echo Starting service...
%NSSM% start %SERVICE_NAME%

echo.
echo ============================================================
echo  Service installed and started!
echo  Health: http://localhost:8100/health
echo  Docs:   http://localhost:8100/docs
echo  Logs:   %SCRIPT_DIR%logs\
echo.
echo  Management commands:
echo    nssm stop %SERVICE_NAME%
echo    nssm start %SERVICE_NAME%
echo    nssm restart %SERVICE_NAME%
echo    nssm remove %SERVICE_NAME% confirm
echo ============================================================
echo.
pause
