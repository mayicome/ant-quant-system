@echo off
setlocal

REM Run get_x_following.py every hour on the hour between 19:00 and 06:00
REM Run this batch file from the project root.

cd /d "%~dp0"

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_x_following_hourly.ps1"

