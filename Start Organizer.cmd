@echo off
"%~dp0runtime\python.exe" "%~dp0app\server.py"
if errorlevel 1 pause
