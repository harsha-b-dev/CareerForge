@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Creating .venv...
  python -m venv .venv
)
echo Installing/updating required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Starting Career Prediction app...
echo Open http://127.0.0.1:5000 in your browser.
".venv\Scripts\python.exe" app.py
pause
