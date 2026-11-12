#!/bin/bash
# Запуск RadioPlan на macOS двойным щелчком
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Не найден Python 3. Установите его с https://www.python.org/downloads/ и запустите снова."
  read -r -p "Нажмите Enter для выхода..."
  exit 1
fi
if [ ! -d .venv ]; then
  echo "Первый запуск: устанавливаю зависимости (1–2 минуты)..."
  python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt || {
    echo "Ошибка установки зависимостей."; rm -rf .venv; read -r -p "Нажмите Enter для выхода..."; exit 1; }
fi
exec .venv/bin/python -m streamlit run app.py
