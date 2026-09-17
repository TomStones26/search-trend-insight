@echo off
title Sync latest snapshot to GitHub Pages
setlocal
cd /d "%~dp0"

set "PY=C:\Users\ynwas\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
set "GHCLI=%LOCALAPPDATA%\GitHubDesktop\bin\github.bat"
set "GHAPP=%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe"

echo.
echo  ============================================================
echo    Sync local data  -^>  dist\  -^>  GitHub Pages
echo  ============================================================
echo.
echo  This script does 3 things:
echo.
echo    1. re-export the current data into dist\
echo       (needs the local server running on 127.0.0.1:8848)
echo    2. git add + commit dist\
echo    3. git push  -^>  GitHub Pages redeploys by itself
echo.
echo  If step 1 fails, the local server is not running:
echo    double-click start.bat, click "Refresh data" on the home
echo    page, then run this script again.
echo.
echo  Note: GitHub Pages only shows a snapshot. It never updates
echo  by itself - that is what this script is for.
echo.
pause

echo.
echo  [1/3] exporting static site to dist\ ...
"%PY%" export_static.py
if errorlevel 1 goto exportfailed

echo.
echo  [2/3] committing ...
git add -A dist
git -c core.quotepath=false commit -m "chore: sync latest snapshot to static site"
if errorlevel 1 echo   nothing to commit - the data has not changed.

echo.
echo  [3/3] pushing ...
git push
if errorlevel 1 goto pushfailed

echo.
echo  ============================================================
echo    Done.
echo    Deploy progress: https://github.com/TomStones26/search-trend-insight/actions
echo    Live site:       https://tomstones26.github.io/search-trend-insight/
echo  ============================================================
echo.
pause
exit /b 0

:exportfailed
echo.
echo  Export failed - the local server is probably not running.
echo    1. double-click start.bat
echo    2. click "Refresh data" on the home page
echo    3. run this script again
echo.
pause
exit /b 1

:pushfailed
echo.
echo  Push failed - a one-time GitHub login is needed, or the
echo  network is blocked.
echo.
echo  Opening GitHub Desktop for you - click "Push origin" there.
echo.
if exist "%GHCLI%" (
    call "%GHCLI%" open "%CD%"
) else (
    start "" "%GHAPP%" "%CD%"
)
echo.
pause
exit /b 1
