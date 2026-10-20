@echo off
rem PhotoCrate launcher: first run creates .venv, installs deps, downloads ExifTool
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1"
if errorlevel 1 pause
