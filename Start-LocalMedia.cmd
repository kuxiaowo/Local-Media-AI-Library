@echo off
cd /d "%~dp0"
call conda run --no-capture-output -n localmedia-v2 python run_localmedia.py %*
if errorlevel 1 pause
