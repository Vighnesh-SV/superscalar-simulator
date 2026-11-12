@echo off
rem Starts the simulator backend and opens the UI in your browser.
cd /d "%~dp0backend"
python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:8000
python -m uvicorn app:app --port 8000
