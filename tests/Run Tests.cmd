@echo off
"%~dp0..\runtime\python.exe" -m unittest discover -s "%~dp0" -v
pause
