@echo off
REM Xenosaga II Extractor — Windows double-click launcher.
REM Starts the local GUI on a free port and opens your browser.
REM The kit needs numpy (requirements.txt). If your Python lacks it, the first
REM run makes a private environment in .venv\ beside this file and installs
REM numpy there; later runs use that environment.

setlocal
cd /d "%~dp0"

set "BASE="
where py >nul 2>nul && set "BASE=py -3"
if not defined BASE (where python >nul 2>nul && set "BASE=python")
set "PY=%BASE%"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY goto nopython

%PY% -c "import numpy" >nul 2>nul
if not errorlevel 1 goto run
if not defined BASE goto noinstall
echo Setting up numpy, which the music renderer needs, in .venv\ ...
%BASE% -m venv .venv
if errorlevel 1 goto noinstall
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto noinstall
set "PY=.venv\Scripts\python.exe"

:run
%PY% gui.py %*
REM If gui.py errored out, keep the window open so the user can see why.
if errorlevel 1 pause
exit /b

:nopython
echo Python 3 was not found on your PATH.
echo Install Python 3.9 or newer from https://www.python.org/downloads/
echo (make sure to tick "Add Python to PATH" during install),
echo then double-click this launcher again.
pause
exit /b 1

:noinstall
echo Could not install numpy automatically. Install it yourself with
echo     py -3 -m pip install -r requirements.txt
echo then double-click this launcher again.
pause
exit /b 1
