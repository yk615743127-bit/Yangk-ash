@echo off
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    echo 未发现 .venv，请先在 PyCharm 中创建 Python 3.12.10 虚拟环境。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py
pause
