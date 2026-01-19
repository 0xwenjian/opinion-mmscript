@echo off
chcp 65001 >nul
echo ========================================
echo   Opinion 快速限价交易机器人
echo ========================================
echo.

cd /d "%~dp0"

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python
    pause
    exit /b 1
)

REM 运行交易机器人
python trader.py %*

pause
