@echo off
title Push to GitHub
cd /d "D:\WorkBuddyWorkspace\2026-10-17-20-26-43\retail-nl2sql"

set "REPO=retail-nl2sql"

echo.
echo   ==========================================
echo       Push project to GitHub
echo   ==========================================
echo.

where gh >nul 2>&1
if errorlevel 1 (
    echo   GitHub CLI not found. Install from https://cli.github.com/
    echo.
    pause
    exit /b 1
)

gh auth status >nul 2>&1
if errorlevel 1 (
    echo   Not logged in yet. A browser page will open next.
    echo   Select: GitHub.com  -  HTTPS  -  Login with a web browser
    echo   Then paste the one-time code shown here.
    echo.
    gh auth login
    echo.
    echo   If login succeeded, run this file again.
    pause
    exit /b 0
)

echo   Logged in. Creating repo and pushing ...
echo.

gh repo create %REPO% --public --source=. --remote=origin --push

if errorlevel 1 (
    echo.
    echo   Create failed. If the repo already exists, push manually:
    echo     git remote add origin https://github.com/YOURNAME/%REPO%.git
    echo     git push -u origin main
    echo.
    pause
    exit /b 1
)

echo.
echo   ==========================================
echo       Done. Repository URL:
echo   ==========================================
gh repo view --json url -q .url
echo.
pause
