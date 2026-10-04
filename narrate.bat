@echo off
rem Drag an ebook onto this file, or run:  narrate book.epub [options]
"%~dp0.venv\Scripts\python.exe" "%~dp0narrate.py" %*
if "%~1" neq "" if "%~2"=="" pause
