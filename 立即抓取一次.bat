@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 立即抓取一次（不打开界面）

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo 未找到 Python 环境。
    echo 请改用界面里的「立即抓取」按钮。
    echo.
    pause
    goto :eof
)

python "%~dp0src\pipeline.py"
echo.
pause
