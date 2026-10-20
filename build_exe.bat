@echo off
rem Build dist\PhotoCrate\PhotoCrate.exe with PyInstaller (and installer if Inno Setup 6 is installed)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" -Tool pyinstaller
