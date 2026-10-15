@echo off
chcp 936 >nul
cd /d "%~dp0"

set HF_ENDPOINT=https://hf-mirror.com
REM 镜像不支持 Xet 传输协议，必须关闭，否则下载模型会报 JSON 解析错误
set HF_HUB_DISABLE_XET=1

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

echo ============================================
echo   校园智能助手 - 构建知识索引
echo ============================================
echo.
echo 正在扫描 data\raw\ 下的校园文档并向量化，首次运行需下载嵌入模型...
echo.

"%PY%" -m app.cli build

echo.
pause
