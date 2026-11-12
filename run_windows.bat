@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Первый запуск: устанавливаю зависимости...
  python -m venv .venv || (echo Нужен Python 3.10+ с python.org & pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (echo [general]& echo email = "")> "%USERPROFILE%\.streamlit\credentials.toml"
.venv\Scripts\python -m streamlit run app.py
pause
