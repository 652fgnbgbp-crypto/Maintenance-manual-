@echo off
title Vantage
cd /d "%~dp0"
rem Prefer a well-supported Python version, then fall back to the newest one installed.
for %%V in (3.13 3.12 3.11 3.14) do (
  py -V:%%V -c "pass" >nul 2>nul
  if not errorlevel 1 (
    py -V:%%V launcher.py %*
    goto :end
  )
)
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
