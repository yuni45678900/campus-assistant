@echo off
chcp 936 >nul
cd /d "%~dp0"
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DISABLE_XET=1
echo ============================================
echo   校园智能助手 - 首次环境安装
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 python 命令，请先安装 Python 3.10+ 并勾选 "Add to PATH"
    echo        下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 创建虚拟环境 .venv ...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
) else (
    echo       已存在，跳过
)

echo [2/3] 升级 pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] 安装依赖（torch 约 2GB，请耐心等待）...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo ============================================
echo   安装完成。下一步：
echo     1) 把校园文档放进 data\raw\
echo     2) 双击 build.bat 构建索引
echo     3) 双击 start.bat 启动服务
echo.
echo   还需要安装 Ollama 并拉取模型：
echo     https://ollama.com/download
echo     ollama pull qwen3:8b
echo ============================================
pause
