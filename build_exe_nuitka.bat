@echo off
rem Alternative build with Nuitka (compiled, faster startup; needs Python 3.12 or older)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" -Tool nuitka
