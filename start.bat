@echo off
chcp 936 >nul
cd /d "%~dp0"

REM ── 模型常驻与镜像优化 ─────────────────────────────
REM KEEP_ALIVE=-1  让模型常驻显存（GPU），避免从显存卸载后重新加载权重（首条即秒回）
REM NUM_PARALLEL=1 单并发，防止并发请求把内存吃满
REM HF_ENDPOINT    模型下载走国内镜像
REM HF_HUB_DISABLE_XET  新版 hf_hub 默认的 Xet 协议不被镜像支持，必须关掉
set OLLAMA_KEEP_ALIVE=-1
set OLLAMA_NUM_PARALLEL=1
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DISABLE_XET=1

echo ============================================
echo   校园多助手智能问答系统 - 启动
echo ============================================
echo.

REM ── 1. 检查 Python 环境 ──────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo  [错误] 还没有安装 Python 依赖
    echo         请先双击运行  setup.bat  ^(首次约需 5-10 分钟^)
    echo.
    pause
    exit /b 1
)
set PY=.venv\Scripts\python.exe

REM ── 2. 检查关键依赖 ─────────────────────────────
"%PY%" -c "import fastapi, sentence_transformers, jieba, rank_bm25" >nul 2>&1
if errorlevel 1 (
    echo  [错误] 依赖不完整或已损坏，请重新运行  setup.bat
    echo.
    pause
    exit /b 1
)
echo  [OK] Python 环境正常

REM python-multipart 只影响管理端的标准上传通道；缺失时页面会自动降级到
REM 零依赖的裸流通道，服务本身照常运行，所以这里只提示、不拦截。
"%PY%" -c "import multipart" >nul 2>&1
if errorlevel 1 (
    echo  [提示] 未安装 python-multipart，管理端将使用裸流上传通道
    echo         想要标准上传通道请执行： .venv\Scripts\python.exe -m pip install python-multipart
)

REM ── 3. 检查 Ollama 服务 ─────────────────────────
"%PY%" -c "import httpx; httpx.get('http://127.0.0.1:11434/api/version', timeout=3)" >nul 2>&1
if errorlevel 1 (
    echo  [提示] Ollama 未运行，正在尝试启动...
    start "" /min ollama serve
    timeout /t 6 /nobreak >nul
) else (
    echo  [OK] Ollama 服务正常
)

REM ── 4. 检查模型是否已拉取 ───────────────────────
"%PY%" -c "import httpx,sys; d=httpx.get('http://127.0.0.1:11434/api/tags',timeout=5).json(); ms=[m['name'] for m in d.get('models',[])]; sys.exit(0 if any(m.startswith('qwen3') for m in ms) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  [警告] 未检测到 qwen3 模型，回答会失败
    echo         请另开一个终端执行： ollama pull qwen3:8b
    echo.
) else (
    echo  [OK] 模型 qwen3 已就绪
)

REM ── 5. 检查知识库索引 ───────────────────────────
if not exist "data\index\manifest.json" (
    echo  [提示] 还没有知识库索引，正在自动构建...
    "%PY%" -m app.cli build
    echo.
) else (
    echo  [OK] 知识库索引已存在
)

echo.
echo   入口门户： http://127.0.0.1:8000        ^(在这里登录^)
echo   学生端  ： http://127.0.0.1:8000/student  ^(学生账号或管理账号^)
echo   管理端  ： http://127.0.0.1:8000/admin    ^(仅管理账号^)
echo   账号密码： 见 .env（默认 admin/admin11 、 student/student11）
echo   停止服务： 按 Ctrl+C
echo   首次启动会预热模型，约需 30~60 秒，请耐心等待...
echo.

start "" "http://127.0.0.1:8000"

"%PY%" -m app.server --host 127.0.0.1 --port 8000

pause
