@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 抖音播放量监控

rem ---- 1) 优先使用打包好的 exe ----
if exist "抖音监控.exe" (
    echo 正在启动，界面会自动在浏览器中打开 ...
    "抖音监控.exe"
    goto :eof
)
if exist "dist\抖音监控\抖音监控.exe" (
    echo 正在启动，界面会自动在浏览器中打开 ...
    "dist\抖音监控\抖音监控.exe"
    goto :eof
)

rem ---- 2) 退回源码模式，用系统 PATH 里的 python ----
where python >nul 2>nul
if errorlevel 1 goto nopython
echo 以源码模式启动 ...
python "%~dp0src\app.py"
goto :eof

:nopython
echo.
echo 没有找到 抖音监控.exe，也没有找到 Python。
echo.
echo 请到项目的 Releases 页面下载发布包，解压后直接双击 抖音监控.exe。
echo 或者安装 Python 后执行：python src\app.py
echo.
pause
