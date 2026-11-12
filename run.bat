@echo off
rem Start TadDee on this PC: http://localhost:8040 (other PCs: http://PC-NAME:8040)
rem First run makes a Python virtual environment in .venv and installs the packages.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Making the Python environment in .venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Could not make .venv. Install Python 3.11 or newer from python.org and try again.
    pause
    exit /b 1
  )
)
echo Installing packages ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo Package install failed. Check the internet connection and try again.
  pause
  exit /b 1
)
if "%TADDEE_PORT%"=="" set TADDEE_PORT=8040
echo Starting TadDee on port %TADDEE_PORT% ...
".venv\Scripts\python.exe" -m app
pause
