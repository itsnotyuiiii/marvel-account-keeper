@echo off
REM Run Marvel Account Keeper from source (no build needed).
REM app.py picks a free port and opens your browser automatically.
cd /d "%~dp0"
python app.py
