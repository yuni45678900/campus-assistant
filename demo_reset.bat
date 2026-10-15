@echo off
REM 演示前复位：把知识库清回 0 分类 / 0 文档，资料搬到 演示素材\ 备用。
REM 本文件刻意只写 ASCII —— 中文说明全部由 Python 脚本输出，
REM 避免 .bat 编码与 chcp 对不上导致控制台乱码。
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python venv not found. Run setup.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" tools\demo_reset.py %*

echo.
pause
