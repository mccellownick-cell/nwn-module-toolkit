@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === NWN Module Toolkit: проверка установки ===
where py >nul 2>nul && (py -3 --version) || (where python >nul 2>nul && python --version) || (
  echo Python не найден. Установите Python 3.10+ с python.org и включите "Add python.exe to PATH".
  pause & exit /b 1
)
if not exist "nwn_workspace" mkdir "nwn_workspace"
if not exist "nwn_workspace\harness.json" if exist "harness.json.example" copy /y "harness.json.example" "nwn_workspace\harness.json" >nul
echo.
echo Готово. Ассистент (необязательно): вставьте ключ в nwn_workspace\harness.json
echo Запуск: run_dashboard.bat    Анализ: python nwn_analyse.py "путь\к\модулю.mod"
pause