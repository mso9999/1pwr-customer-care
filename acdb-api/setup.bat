@echo off
REM ============================================================
REM ACDB Customer Lookup API - Setup & Run
REM ============================================================
REM Run this on the ACDB Windows EC2 instance.
REM
REM Prerequisites:
REM   - Python 3.9+ installed
REM   - Microsoft Access Database Engine 2016 (or the Access ODBC driver)
REM   - tuacc.accdb present at the expected path
REM
REM This script:
REM   1. Creates a virtual environment (if not exists)
REM   2. Installs dependencies
REM   3. Starts the API on port 8100
REM ============================================================

echo.
echo ============================================================
echo  ACDB Customer Lookup API - Setup
echo ============================================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.9+ and add to PATH.
    pause
    exit /b 1
)

REM Create venv if needed
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate and install
echo Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet

REM Auto-detect DB path
if not defined ACDB_PATH (
    if exist "C:\Users\Administrator\Desktop\AccessDB_Clone\tuacc.accdb" (
        set ACDB_PATH=C:\Users\Administrator\Desktop\AccessDB_Clone\tuacc.accdb
    )
)

echo.
echo ============================================================
echo  Starting API on port 8100...
echo  DB Path: %ACDB_PATH%
echo  Health: http://localhost:8100/health
echo  Docs:   http://localhost:8100/docs
echo ============================================================
echo.

python customer_api.py
