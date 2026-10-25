@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 没有找到 python 命令。
    echo 请先安装 Python 3.8+ 并勾选 "Add python.exe to PATH"。
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 正在启动 QQ Warframe 查询机器人...
python run.py
echo.
pause
