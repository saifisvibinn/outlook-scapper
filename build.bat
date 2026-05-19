@echo off
cd /d "%~dp0"
title OWA Scraper Build
echo.
echo Starting build...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
set BUILD_EXIT=%ERRORLEVEL%

echo.
if %BUILD_EXIT% neq 0 (
    echo Build exited with error code %BUILD_EXIT%.
) else (
    echo Build finished.
)
echo.
pause
exit /b %BUILD_EXIT%
