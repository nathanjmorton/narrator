@echo off
rem Installs everything Narrator needs (first time only).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
