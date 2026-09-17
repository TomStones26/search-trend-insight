@echo off
rem ============================================================
rem  DataPulse - one click launcher (Windows)
rem  Starts the local server and opens the browser.
rem
rem  IMPORTANT: do NOT open index.html by double-clicking it.
rem  The pages load their data from this local server, so
rem  file:// would show an empty page. Always use the URL.
rem ============================================================
cd /d "%~dp0"

set "PY=C:\Users\ynwas\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   Starting DataPulse ...
echo   URL: http://127.0.0.1:8848/
echo.
echo   Keep this window open while you use the site.
echo   Press Ctrl+C or close this window to stop the server.
echo.

"%PY%" server.py --open

echo.
echo   Server stopped.
pause
