@echo off
rem Starts Narrator (run Setup.bat once first).
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Narrator isn't set up yet. Run Setup.bat first.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py" %*
