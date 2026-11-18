#!/usr/bin/env bash
# Запуск Mercury Studio на Linux/macOS (и в Termux через браузер).
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
echo "→ Mercury Studio"
echo "  окно откроется в браузере; чтобы получить нативное окно: pip install pywebview"
exec "$PY" app.py "$@"
