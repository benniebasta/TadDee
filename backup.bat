@echo off
rem Back up TadDee: database + job files into one zip (keeps the newest 14).
rem Folder: TADDEE_BACKUP_DIR, default "D:\Site backups\nest". Data: TADDEE_DATA_DIR, default .\data
rem Restore:  backup.bat --restore "D:\Site backups\nest\nest-YYYYMMDD-HHMMSS-mmm.zip" --to C:\nest\data-restored
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m app.backup %*
exit /b %errorlevel%
