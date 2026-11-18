@echo off
setlocal EnableExtensions
chcp 65001 >nul
title MRCAgent - сборка exe из исходников
echo.
echo   Сборка MRCAgent.exe из этой папки с исходниками.
echo.

set "PY="
call :findpy
if not defined PY goto :nopython
goto :haspy

:findpy
set "PY="
call :trypy py -3
if defined PY exit /b 0
call :trypy python
if defined PY exit /b 0
call :tryexe "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :tryexe "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :tryexe "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :tryexe "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
call :tryexe "%ProgramFiles%\Python312\python.exe"
call :tryexe "%ProgramFiles%\Python311\python.exe"
exit /b 0

:trypy
set "CAND=%~1 %~2"
%CAND% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%CAND%"
exit /b 0

:tryexe
set "CAND=%~1"
if not exist "%CAND%" exit /b 0
"%CAND%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%CAND%"
exit /b 0

:nopython
echo   [!] Python 3.10+ не найден.
echo       Если команда python открывает Microsoft Store - это заглушка магазина.
echo       Поставьте настоящий Python с python.org и включите "Add python.exe to PATH".
pause & exit /b 1

:haspy

cd /d "%~dp0"
echo   [1/3] пакеты...
%PY% -m venv .venv
if errorlevel 1 (
  echo   [!] не удалось создать окружение Python.
  pause & exit /b 1
)
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install --quiet --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo   [!] не удалось установить пакеты.
  pause & exit /b 1
)

echo   [1b] загрузка локального голоса Руслан...
"%VPY%" download_voice.py
if errorlevel 1 (
  echo   [!] голос не скачан. Проверьте подключение к интернету.
  pause & exit /b 1
)

echo   [2/3] иконка...
"%VPY%" make_icon.py
if errorlevel 1 (
  echo   [!] не удалось создать иконку.
  pause & exit /b 1
)

echo   [3/3] сборка...
"%VPY%" -m PyInstaller mrcagent.spec --noconfirm --clean
if errorlevel 1 (
  echo   [!] PyInstaller завершился с ошибкой.
  pause & exit /b 1
)
if exist "%~dp0dist\MRCAgent.exe" (
  copy /y "%~dp0dist\MRCAgent.exe" "%~dp0MRCAgent.exe" >nul
  if errorlevel 1 (
    echo   [!] не удалось скопировать файл EXE.
    pause & exit /b 1
  )
  echo.
  echo   ГОТОВО: %~dp0MRCAgent.exe
) else (
  echo   [!] сборка не удалась, смотрите вывод выше.
  pause & exit /b 1
)
pause
exit /b 0
