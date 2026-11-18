@echo off
chcp 65001 >nul
title MRC Agent
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python не найден — поставьте Python 3.10+ с python.org
  pause & exit /b 1
)
echo Запускаю MRC Agent (агенты продолжат работать после закрытия окна)...
python app.py
if errorlevel 1 pause
