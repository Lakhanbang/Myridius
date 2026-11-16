@echo off
setlocal
set PYTHONIOENCODING=utf-8
call .\venv\Scripts\activate.bat
python run.py %*
endlocal
