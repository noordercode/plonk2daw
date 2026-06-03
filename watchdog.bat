@echo off
rem plonk2daw watchdog launcher -- runs watch.py with this folder's venv Python.
title plonk2daw watchdog
if not exist "%~dp0.venv\Scripts\python.exe" goto novenv
"%~dp0.venv\Scripts\python.exe" "%~dp0watch.py" %*
echo.
echo (plonk2daw watchdog stopped)
pause
exit /b 0

:novenv
echo.
echo   No virtual environment found here (.venv\Scripts\python.exe).
echo   Do the one-time setup first (see README "Setup"):
echo.
echo       python -m venv .venv
echo       .venv\Scripts\activate
echo       pip install -r requirements.txt
echo       python generate_stubs.py --proto "C:\path\to\PTSL.proto"
echo.
echo   Or just run it with your own Python:  python watch.py
echo.
pause
exit /b 1
