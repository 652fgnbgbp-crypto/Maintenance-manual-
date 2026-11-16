@echo off
title Vantage
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 launcher.py %*
  goto :end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python launcher.py %*
  goto :end
)
echo Vantage needs Python 3.10 or newer.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then try again.
pause
:end
