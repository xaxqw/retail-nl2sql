@echo off
title Retail NL2SQL - Launcher
cd /d "D:\WorkBuddyWorkspace\2026-10-17-20-26-43\retail-nl2sql"

set "PY=C:\Users\xuanx\AppData\Local\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=C:\Users\xuanx\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" (
    echo.
    echo   Python not found. Open run.bat in Notepad and fix the PY path.
    echo.
    pause
    exit /b 1
)

echo.
echo   ==========================================
echo       Retail Data NL2SQL  -  Launcher
echo   ==========================================
echo.

rem --- already running? just open the browser, never start a second copy ---
curl -s --max-time 2 http://127.0.0.1:8000/api/health >nul 2>&1
if not errorlevel 1 (
    echo   Server is already running. Opening browser ...
    start "" http://127.0.0.1:8000
    timeout /t 2 /nobreak >nul
    exit
)

echo   [1/3] Checking local model service ...
curl -s --max-time 3 http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo         Ollama not running, starting it ...
    start "" "C:\Users\xuanx\AppData\Local\Programs\Ollama\ollama app.exe"
    timeout /t 8 /nobreak >nul
) else (
    echo         Ollama is running
)

echo   [2/3] Starting query server ...
start "NL2SQL Server - keep this window" /min "%PY%" -u server.py

echo   [3/3] Opening browser ...
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8000

echo.
echo   Done.
echo   - First query takes 10-20s to load the model, then 2-6s each
echo   - To stop: close the minimized "NL2SQL Server" window
echo   - Project folder: D:\WorkBuddyWorkspace\2026-10-17-20-26-43\retail-nl2sql
echo.
timeout /t 10 >nul
exit
