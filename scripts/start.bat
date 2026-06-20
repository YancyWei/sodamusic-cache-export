@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
cd /d "%ROOT_DIR%"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 src\start_sodamusic_export.py %*
  goto done
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python src\start_sodamusic_export.py %*
  goto done
)

echo 未找到 Python 3，请先安装 Python。
exit /b 1

:done
exit /b %ERRORLEVEL%
