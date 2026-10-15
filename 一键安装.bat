@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 校园多助手智能问答系统 · 一键安装

REM ============================================================
REM  校园多助手智能问答系统 —— 一键安装 / 环境体检
REM
REM  用法（直接双击 = 全自动装齐并启动）：
REM      双击                 装齐所有依赖并启动服务
REM      一键安装.bat check   只体检不安装，看看缺什么
REM      一键安装.bat noboot  装齐但先不启动
REM
REM  自动处理这些事：
REM    1. .env 配置文件       缺失则生成（admin/admin11、student/student11）
REM    2. Python 3.10+        没有就静默安装（走国内镜像）
REM    3. 虚拟环境 .venv      缺失或损坏（比如从别人电脑拷来的）自动重建
REM    4. Python 依赖         走清华镜像装 fastapi / torch / sentence-transformers
REM    5. Ollama + 对话模型    没装就静默安装，再拉取 qwen3:8b
REM    6. 知识索引            缺失就自动构建
REM    7. 启动服务并打开浏览器
REM ============================================================

REM ---- 可调参数（一般不用改）--------------------------------
set "PY_VER=3.12.8"
set "PY_URL=https://mirrors.huaweicloud.com/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "PY_URL2=https://registry.npmmirror.com/-/binary/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "OLLAMA_URL=https://ollama.com/download/OllamaSetup.exe"
set "OLLAMA_GH=https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe"
set "LLM_MODEL=qwen3:8b"
set "ENDPOINT=http://127.0.0.1:8000"
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HUB_DISABLE_XET=1"
set "OLLAMA_KEEP_ALIVE=-1"
set "OLLAMA_NUM_PARALLEL=1"

set "MODE=install"
if /i "%~1"=="check"  set "MODE=check"
if /i "%~1"=="noboot" set "MODE=noboot"

echo ============================================================
echo    校园多助手智能问答系统 · 一键安装
echo ============================================================
echo.

REM ============================================================
REM  0. 包体完整性
REM ============================================================
if not exist "app\server.py" (
    echo  [错误] 这里不是项目根目录：找不到 app\server.py
    echo         请把本文件放回项目根目录（与 app、web、data 同级）再运行。
    echo.
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo  [错误] 找不到 requirements.txt，包可能没解压完整。
    echo.
    pause
    exit /b 1
)

REM ============================================================
REM  1. 环境体检（先探测，再决定装什么）
REM ============================================================
echo  [1/6] 体检本机环境 ...

set "BASE_PY="
for /f "delims=" %%P in ('where python 2^>nul') do if not defined BASE_PY call :probe_py "%%P"
for /f "delims=" %%P in ('where python3 2^>nul') do if not defined BASE_PY call :probe_py "%%P"
if not defined BASE_PY call :probe_py "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined BASE_PY call :probe_py "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined BASE_PY call :probe_py "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined BASE_PY call :probe_py "%ProgramFiles%\Python312\python.exe"
if not defined BASE_PY call :probe_py "%ProgramFiles%\Python311\python.exe"

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "VENV_OK="
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "VENV_OK=1"
)

set "DEPS_OK="
if defined VENV_OK (
    "%VENV_PY%" -c "import fastapi,sentence_transformers,jieba,rank_bm25,multipart" >nul 2>&1
    if not errorlevel 1 set "DEPS_OK=1"
)

set "OLLAMA_EXE="
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA_EXE if exist "%ProgramFiles%\Ollama\ollama.exe" set "OLLAMA_EXE=%ProgramFiles%\Ollama\ollama.exe"
if not defined OLLAMA_EXE for /f "delims=" %%P in ('where ollama 2^>nul') do if not defined OLLAMA_EXE set "OLLAMA_EXE=%%P"

set "OLLAMA_UP="
if defined OLLAMA_EXE (
    curl -s --max-time 4 -o NUL "http://127.0.0.1:11434/api/version" >nul 2>&1
    if not errorlevel 1 set "OLLAMA_UP=1"
)

REM 查模型走 HTTP 接口，而不解析 "ollama list" 的文本输出 ——
REM CLI 版本和守护进程不一致时会直接报错，用它判断会误报"模型未拉取"。
set "LLM_OK="
if defined OLLAMA_UP (
    curl -s --max-time 6 "http://127.0.0.1:11434/api/tags" 2>nul | findstr /i "qwen3" >nul 2>&1
    if not errorlevel 1 set "LLM_OK=1"
)

set "MODELS_OK="
if exist "data\models\BAAI--bge-small-zh-v1.5" set "MODELS_OK=1"
if not exist "data\models\BAAI--bge-reranker-base" set "MODELS_OK="

set "INDEX_OK="
if exist "data\index\manifest.json" set "INDEX_OK=1"

set "ENV_OK="
if exist ".env" set "ENV_OK=1"

echo.
echo ------------------------------------------------------------
echo    环境体检结果
echo ------------------------------------------------------------
REM 先把每行结论算进变量，再逐行打印 —— 避免在 echo 里嵌 if/else 导致括号解析出错
set "L_PY=[缺]   Python         未检测到 3.10 以上版本（将自动安装）"
if defined BASE_PY set "L_PY=[OK]   Python         !BASE_PY!"

set "L_VENV=[缺]   虚拟环境       .venv 不存在或已损坏（将自动重建）"
if defined VENV_OK set "L_VENV=[OK]   虚拟环境       .venv 可用"

set "L_DEPS=[缺]   Python 依赖    未装齐（首次约 2GB）"
if defined DEPS_OK set "L_DEPS=[OK]   Python 依赖    已装齐"

set "L_OLLAMA=[缺]   Ollama         未安装（约 700MB，将自动安装）"
if defined OLLAMA_EXE set "L_OLLAMA=[OK]   Ollama         !OLLAMA_EXE!"

set "L_LLM=[  ]   对话模型       Ollama 当前没在运行，安装时会先启动它再检查"
if not defined OLLAMA_EXE set "L_LLM=[  ]   对话模型       装好 Ollama 之后才能检查"
if defined OLLAMA_UP if defined LLM_OK set "L_LLM=[OK]   对话模型       %LLM_MODEL% 已就绪"
if defined OLLAMA_UP if not defined LLM_OK set "L_LLM=[缺]   对话模型       %LLM_MODEL% 未拉取（约 5GB）"

set "L_MODELS=[缺]   嵌入/重排模型   缺失（联网自动下载约 1.3GB）"
if defined MODELS_OK set "L_MODELS=[OK]   嵌入/重排模型   data\models 已就绪"

set "L_INDEX=[缺]   知识索引       未构建（将自动构建）"
if defined INDEX_OK set "L_INDEX=[OK]   知识索引       已构建"

set "L_ENV=[缺]   配置文件       .env 缺失（将自动生成）"
if defined ENV_OK set "L_ENV=[OK]   配置文件       .env 已存在"

echo   !L_PY!
echo   !L_VENV!
echo   !L_DEPS!
echo   !L_OLLAMA!
echo   !L_LLM!
echo   !L_MODELS!
echo   !L_INDEX!
echo   !L_ENV!
echo ------------------------------------------------------------
echo.

if /i "%MODE%"=="check" (
    echo  体检完毕，未改动任何东西。想正式安装请直接双击本文件。
    echo.
    pause
    exit /b 0
)

REM ============================================================
REM  2. 配置文件 .env
REM ============================================================
if not defined ENV_OK (
    echo  [2/6] 生成配置文件 .env ...
    > ".env" echo # 本文件由 一键安装.bat 自动生成，可自行修改
    >> ".env" echo CAMPUS_ADMIN_USER=admin
    >> ".env" echo CAMPUS_ADMIN_PASS=admin11
    >> ".env" echo CAMPUS_STUDENT_USER=student
    >> ".env" echo CAMPUS_STUDENT_PASS=student11
    >> ".env" echo CAMPUS_STUDENT_SCHOOL=演示学校
    echo        已生成（管理端 admin/admin11，学生端 student/student11）
) else (
    echo  [2/6] .env 已存在，跳过
)

REM ============================================================
REM  3. Python 3.10+
REM ============================================================
if not defined BASE_PY (
    echo  [3/6] 未检测到 Python 3.10+，自动安装 Python %PY_VER% ...
    set "PYINST=%TEMP%\python-%PY_VER%-amd64.exe"
    if exist "!PYINST!" del "!PYINST!" >nul 2>&1
    call :download "!PY_URL!" "!PYINST!"
    if not exist "!PYINST!" call :download "!PY_URL2!" "!PYINST!"
    if not exist "!PYINST!" (
        echo.
        echo  [错误] Python 安装包下载失败。
        echo         请手动安装 Python 3.10 以上版本，安装时务必勾选 Add to PATH：
        echo         https://www.python.org/downloads/
        echo         装好后重新双击本文件即可。
        goto :fail
    )
    echo        正在静默安装，约 1~2 分钟，请勿关闭窗口 ...
    "!PYINST!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
    timeout /t 5 /nobreak >nul
    if not defined BASE_PY call :probe_py "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not defined BASE_PY (
        echo.
        echo  [错误] Python 装完了但没找到，请重启电脑后重新双击本文件。
        goto :fail
    )
    echo        已安装：!BASE_PY!
) else (
    echo  [3/6] Python 已就绪，跳过安装
)

REM ============================================================
REM  4. 虚拟环境 + Python 依赖
REM ============================================================
if not defined VENV_OK (
    if exist ".venv" (
        echo  [4/6] 现有 .venv 不可用（可能是从别的电脑拷来的），清理重建 ...
        rmdir /s /q ".venv" >nul 2>&1
    ) else (
        echo  [4/6] 创建虚拟环境 .venv ...
    )
    "!BASE_PY!" -m venv .venv
    if not exist "%VENV_PY%" (
        echo  [错误] 虚拟环境创建失败。
        goto :fail
    )
    echo        虚拟环境已就绪
) else (
    echo  [4/6] 虚拟环境已就绪，跳过创建
)

if defined DEPS_OK (
    echo        Python 依赖已装齐，跳过安装
) else (
    echo        安装 Python 依赖（torch 约 2GB，首次较慢，可先泡杯茶）...
    echo        镜像：%PIP_MIRROR%
    "%VENV_PY%" -m pip install --upgrade pip -i %PIP_MIRROR% --quiet
    "%VENV_PY%" -m pip install -r requirements.txt -i %PIP_MIRROR%
    if errorlevel 1 (
        echo.
        echo  [错误] 依赖安装失败（多半是网络中断）。联网后重新双击本文件即可接着装。
        goto :fail
    )
    "%VENV_PY%" -c "import fastapi,sentence_transformers,jieba,rank_bm25" >nul 2>&1
    if errorlevel 1 (
        echo  [错误] 依赖仍不完整，请重跑本文件。
        goto :fail
    )
    echo        依赖安装完成
)

REM ============================================================
REM  5. Ollama + 对话模型
REM ============================================================
if not defined OLLAMA_EXE (
    echo  [5/6] 未检测到 Ollama，准备安装 ...
    if exist "OllamaSetup.exe" (
        echo        使用本目录下的 OllamaSetup.exe 安装（免下载）
        set "OSE=%CD%\OllamaSetup.exe"
    ) else (
        set "OSE=%TEMP%\OllamaSetup.exe"
        if exist "!OSE!" del "!OSE!" >nul 2>&1
        call :download "!OLLAMA_URL!" "!OSE!"
        if not exist "!OSE!" call :download "!OLLAMA_GH!" "!OSE!"
        if not exist "!OSE!" (
            echo.
            echo  [错误] Ollama 安装包下载失败（它托管在 GitHub，国内常连不上）。
            echo         三个办法任选：
            echo           1. 手动下载安装 https://ollama.com/download ，装完重跑本文件
            echo           2. 让发你文件的人把 OllamaSetup.exe 一起放进来，再重跑
            echo           3. 挂上代理后重跑本文件
            goto :fail
        )
    )
    echo        正在静默安装 Ollama，约 1~2 分钟 ...
    "!OSE!" /VERYSILENT /NORESTART
    timeout /t 8 /nobreak >nul
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    if not defined OLLAMA_EXE if exist "%ProgramFiles%\Ollama\ollama.exe" set "OLLAMA_EXE=%ProgramFiles%\Ollama\ollama.exe"
    if not defined OLLAMA_EXE (
        echo.
        echo  [错误] Ollama 安装完但没找到 ollama.exe。请手动启动一次 Ollama，再重跑本文件。
        goto :fail
    )
    echo        已安装：!OLLAMA_EXE!
) else (
    echo  [5/6] Ollama 已就绪，跳过安装
)

REM ---- 确保 Ollama 服务在运行（没起来就绝不往下拉模型）----
curl -s --max-time 4 -o NUL "http://127.0.0.1:11434/api/version" >nul 2>&1
if errorlevel 1 (
    echo        启动 Ollama 后台服务 ...
    start "Ollama" /min "!OLLAMA_EXE!" serve
    timeout /t 8 /nobreak >nul
)

curl -s --max-time 6 -o NUL "http://127.0.0.1:11434/api/version" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [提示] Ollama 服务没能起来，先跳过对话模型这一步。
    echo         请手动打开一次 Ollama（开始菜单里搜 Ollama），再重新双击本文件。
) else (
    curl -s --max-time 6 "http://127.0.0.1:11434/api/tags" 2>nul | findstr /i "qwen3" >nul 2>&1
    if not errorlevel 1 (
        echo        对话模型 %LLM_MODEL% 已就绪
    ) else (
        echo.
        echo        正在拉取对话模型 %LLM_MODEL%（约 5GB，视网速十几分钟到半小时）
        echo        嫌久可按 Ctrl+C 中断，之后手动执行：  ollama pull %LLM_MODEL%
        echo.
        "!OLLAMA_EXE!" pull %LLM_MODEL%
        curl -s --max-time 6 "http://127.0.0.1:11434/api/tags" 2>nul | findstr /i "qwen3" >nul 2>&1
        if errorlevel 1 (
            echo.
            echo  [提示] 模型还没拉下来。服务仍能启动，但问答会提示"模型未就绪"；
            echo         联网后执行  ollama pull %LLM_MODEL%  即可补齐。
        )
    )
)

REM ============================================================
REM  6. 知识索引
REM ============================================================
if not exist "data\index\manifest.json" (
    echo  [6/6] 构建知识索引（首次要加载嵌入模型，约 1~3 分钟）...
    "%VENV_PY%" -m app.cli build
    if errorlevel 1 (
        echo  [错误] 索引构建失败，请检查 data\raw 下是否有文档。
        goto :fail
    )
) else (
    echo  [6/6] 知识索引已存在，跳过
)

echo.
echo ============================================================
echo    安装完成
echo ============================================================
echo    管理端： %ENDPOINT%/admin      账号 admin   / admin11
echo    学生端： %ENDPOINT%/student    账号 student / student11
echo    首页  ： %ENDPOINT%
echo.
echo    以后日常使用直接双击 start.bat 就行，不用再跑本文件。
echo ============================================================
echo.

if /i "%MODE%"=="noboot" (
    echo  已按 noboot 参数跳过启动。手动启动：双击 start.bat
    echo.
    pause
    exit /b 0
)

pause
start "" "%ENDPOINT%"
"%VENV_PY%" -m app.server --host 127.0.0.1 --port 8000
echo.
echo  服务已停止。按任意键关闭窗口。
pause
exit /b 0


REM ============================================================
REM  子程序
REM ============================================================

REM 探测某个 python.exe 是否可用且版本 >= 3.10；可用则记入 BASE_PY
:probe_py
if defined BASE_PY goto :eof
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "BASE_PY=%~1"
goto :eof

REM 下载文件：优先 curl，老系统回退到 powershell
:download
echo        下载 %~nx2 ...
where curl >nul 2>&1
if errorlevel 1 goto :download_ps
curl -L --fail --connect-timeout 20 --retry 2 -o "%~2" "%~1" 2>nul
goto :eof

:download_ps
powershell -NoProfile -Command "try{Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing}catch{exit 1}" >nul 2>&1
goto :eof

:fail
echo.
echo ============================================================
echo    安装中断 —— 解决上面的问题后，重新双击本文件即可继续
echo    （已经装好的步骤会自动跳过，不会重复下载）
echo ============================================================
echo.
pause
exit /b 1
