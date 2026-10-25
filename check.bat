@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 没有找到 python 命令。
    echo 请先安装 Python 3.8+ 并勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)

echo 正在做联网自检（不会连接 QQ）...
python selftest.py
echo.
pause
