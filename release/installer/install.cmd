@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-KoreanPatch.ps1" -Mode Install %*
set "CGKO_EXIT=%errorlevel%"
if /I not "%CGKO_NO_PAUSE%"=="1" pause
exit /b %CGKO_EXIT%
