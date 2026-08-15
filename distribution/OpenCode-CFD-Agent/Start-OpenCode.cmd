@echo off
setlocal
cd /d "%~dp0"

where opencode.cmd >nul 2>&1
if not errorlevel 1 (
    call opencode.cmd %*
    exit /b %errorlevel%
)

if exist "%APPDATA%\npm\opencode.cmd" (
    call "%APPDATA%\npm\opencode.cmd" %*
    exit /b %errorlevel%
)

echo OpenCode was not found on the Windows PATH or under %%APPDATA%%\npm. 1>&2
echo Install the Windows version of OpenCode, then run this launcher again. 1>&2
exit /b 1
