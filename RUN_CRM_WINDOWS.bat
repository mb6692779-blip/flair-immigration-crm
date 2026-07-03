@echo off
title Flair Jarvis CRM Production
cd /d "%~dp0"
if not exist ".venv" py -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
start http://127.0.0.1:5050
python app.py
pause
