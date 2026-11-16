@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d = [Environment]::GetFolderPath('Desktop');" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Vantage.lnk'));" ^
  "$s.TargetPath = (Join-Path (Get-Location) 'Start Vantage (Windows).bat');" ^
  "$s.WorkingDirectory = (Get-Location).Path;" ^
  "$s.IconLocation = (Join-Path (Get-Location) 'desktop\vantage.ico');" ^
  "$s.Description = 'Vantage by Torwood Aerospace Group';" ^
  "$s.Save()"
if %errorlevel%==0 (echo A Vantage icon has been added to your desktop.) else (echo Could not create the desktop icon.)
pause
