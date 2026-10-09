@echo off
rem Runs reelvault.ps1 without changing the system PowerShell execution policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0reelvault.ps1" %*
exit /b %ERRORLEVEL%
