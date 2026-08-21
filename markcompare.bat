@echo off
python "%~dp0markcompare.py" %*
if errorlevel 1 pause
