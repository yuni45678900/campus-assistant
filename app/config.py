# -*- coding: utf-8 -*-
"""
全局配置中心 —— 所有可调参数都在这里，改本文件即可，不用动业务代码。

注意：HF_ENDPOINT 必须在任何 transformers / sentence_transformers 导入之前生效，
      所以本项目所有模块的第一行 import 都是 `from app import config`。
"""
import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 1. 国内网络适配（务必在导入模型库之前执行）
# --------------------------------------------------------------------------
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # HuggingFace 镜像
# 关键：新版 huggingface_hub 默认走 Xet 传输协议，而 hf-mirror 不支持，
# 会返回非 JSON 响应并报 "Expecting value: line 1 column 1"。必须关掉。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# 供 models.py 直接拼接下载地址
HF_ENDPOINT = os.environ["HF_ENDPOINT"].rstrip("/")

# --------------------------------------------------------------------------
# 2. 路径
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"        # ← 校园文档放这里
INDEX_DIR = DATA_DIR / "index"    # 索引持久化目录
MODELS_DIR = DATA_DIR / "models"  # 嵌入/重排模型的本地存放目录
ASSETS_DIR = DATA_DIR / "assets"  # 从文档中提取出的内嵌图片
WEB_DIR = BASE_DIR / "web"
LOG_DIR = DATA_DIR / "logs"
# 上传暂存区：自动归类完成之前，先落在这里。
# 放在 RAW_DIR **之外**是刻意的 —— raw 下的一级目录会被当成助手，
# 暂存目录若在 raw 里，会凭空多出一个「_incoming 助手」。
INCOMING_DIR = DATA_DIR / "incoming"

for _d in (RAW_DIR, INDEX_DIR, MODELS_DIR, ASSETS_DIR, WEB_DIR, LOG_DIR,
           INCOMING_DIR):
    _d.mkdir(parents=True, exist_ok=True)
# TRASH_DIR 延迟创建：没有删除动作时不产生空目录（见 admin.py）

# 小于该体积的内嵌图片视为装饰性图标，不入库
MIN_IMAGE_BYTES = int(os.getenv("CAMPUS_MIN_IMAGE_BYTES", "1500"))

# 索引采用**分库**结构：每个小助手一个子目录 data/index/<助手名>/
# 好处：单个助手可独立重建、互不影响；检索时天然不跨域污染
SUPPORTED_EXT = {".docx", ".doc", ".txt", ".md", ".pdf"}
# 真正能被解析器读懂的扩展名。
# 老版 .doc 不在其中：document.load_any() 对它是直接抛错的（"请另存为 .docx"）。
# 留着它在 SUPPORTED_EXT 里，是为了让管理端能"看得见"这类文件并给出明确提示，
# 而不是让它变成隐形文件；上传时则由 admin.py 直接拒收。
PARSABLE_EXT = {".docx", ".txt", ".md", ".pdf"}

ASSISTANTS_FILE = DATA_DIR / "assistants.json"    # 助手配置（可选）
INDEX_MANIFEST = INDEX_DIR / "manifest.json"      # 已构建助手的清单

# --------------------------------------------------------------------------
# 2.1 管理端
# --------------------------------------------------------------------------
TRASH_DIR = DATA_DIR / "trash"                    # 删除的文档进这里，不真删
MAX_UPLOAD_MB = int(os.getenv("CAMPUS_MAX_UPLOAD_MB", "1024"))   # 单文件上限（默认 1GB）


# 极简 .env 读取：只在项目根放一个 .env（KEY=VALUE 一行一条），
# 不引入 python-dotenv 依赖。已存在的真实环境变量优先级更高，不覆盖。
def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_dotenv()

# --------------------------------------------------------------------------
# 2.2 账号体系 —— 两套凭据，权限是**包含**关系（不是平行关系）
#
#   学生账号  →  只能进学生端；手动敲 /admin 会被服务端打回门户
#   管理账号  →  管理端 + 学生端都能进（管理端侧栏就有「回到学生端」）
#
# 全部写在 .env 里，改完重启服务生效（.env.example 是模板）。
# --------------------------------------------------------------------------
ADMIN_USER = os.getenv("CAMPUS_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("CAMPUS_ADMIN_PASS", "admin11")
# 内置的演示学生账号：即使还没在管理端开过号，也能登进去看学生端长什么样。
# 管理端开出来的账号存在 data/accounts.json 里，和这两个互不影响。
STUDENT_USER = os.getenv("CAMPUS_STUDENT_USER", "student")
STUDENT_PASS = os.getenv("CAMPUS_STUDENT_PASS", "student11")
STUDENT_SCHOOL = os.getenv("CAMPUS_STUDENT_SCHOOL", "演示学校")

# 动态账号表（管理端现场开的学生 / 学校管理员账号）
ACCOUNTS_FILE = DATA_DIR / "accounts.json"

# 站点设置（目前就一项：学生端左上角的校园地图入口）
SETTINGS_FILE = DATA_DIR / "settings.json"

# 登录态 Cookie。值形如 "角色.签发时间戳.HMAC签名" —— 手改成 admin 也过不了验签。
SESSION_COOKIE = "campus_session"
SESSION_TTL = int(os.getenv("CAMPUS_SESSION_HOURS", "8")) * 3600

# 无状态旁路：给脚本 / 第三方调用留的口令（值等于管理密码时可用）。
# 留空则只能用登录接口换 Cookie，页面与脚本一视同仁。
ADMIN_TOKEN = os.getenv("CAMPUS_ADMIN_TOKEN", "")

# 未登录时打回门户的地址；门户根据 locked 参数弹对应的登录框。
LOGIN_DENY_REDIRECT = "/?locked={role}"


def assistant_index_dir(assistant: str) -> Path:
    return INDEX_DIR / assistant


def assistant_index_files(assistant: str) -> dict:
    d = assistant_index_dir(assistant)
    return {
        "dir": d,
        "embeddings": d / "embeddings.npy",
        "chunks": d / "chunks.json",
        "bm25": d / "bm25.pkl",
        "meta": d / "meta.json",
    }

# --------------------------------------------------------------------------
# 3. 大模型（通过 Ollama 本地部署 Qwen3-8B）
# --------------------------------------------------------------------------
def _normalize_ollama_host(raw: str) -> str:
    """把环境变量 OLLAMA_HOST 归一化成可用的客户端 URL。

    ⚠️ Ollama 官方把 OLLAMA_HOST 定义为**服务端监听地址**（形如 0.0.0.0:11434，
    不带协议头），安装程序会把它写进系统环境变量。若直接拿去当客户端 URL 使用：
      1) 缺少 http:// 前缀 → httpx 报 "Request URL is missing an 'http://'..."
      2) 0.0.0.0 / :: 是"监听所有网卡"的语义，作为**连接目标**无效
    这里统一补协议头，并把通配地址换回 127.0.0.1。
    """
    s = (raw or "").strip()
    if not s:
        return "http://127.0.0.1:11434"
    if "://" not in s:
        s = "http://" + s
    scheme, _, rest = s.partition("://")
    hostport = rest.split("/")[0]           # 丢掉可能的路径
    # 兼容 [::]:11434 这类 IPv6 写法，先剥方括号
    probe = hostport.lstrip("[")
    if probe.startswith("0.0.0.0") or probe.startswith("::") or probe.startswith("]"):
        hostport = "127.0.0.1" + (":" + hostport.rsplit(":", 1)[1]
                                  if ":" in hostport else ":11434")
    if ":" not in hostport:
        hostport += ":11434"
    return f"{scheme}://{hostport}"


OLLAMA_HOST = _normalize_ollama_host(os.getenv("OLLAMA_HOST", ""))
LLM_MODEL = os.getenv("CAMPUS_LLM_MODEL", "qwen3:8b")
LLM_TEMPERATURE = float(os.getenv("CAMPUS_LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.getenv("CAMPUS_LLM_MAX_TOKENS", "320"))

# 推理调优项（模型现跑在 GPU 上，以下各项依旧有效）---------------------------
# Qwen3 是混合推理模型，默认会先输出一大段思考链。纯 CPU 环境下这段思考
# 会把首字延迟拉高一倍以上，且校园问答并不需要它，因此默认关闭。
# 若装的是老版本 Ollama（不认识 think 字段），llm.py 会自动降级重试。
LLM_THINK = os.getenv("CAMPUS_LLM_THINK", "0") == "1"
LLM_NUM_CTX = int(os.getenv("CAMPUS_LLM_NUM_CTX", "3072"))   # 上下文窗口
LLM_FORCE_NO_THINK_PROMPT = True    # 兜底：在 prompt 末尾附加 <think:6124c78e></think:6124c78e># 模型常驻显存：消除"首次反应慢"（Ollama 默认 5 分钟空闲就卸载，
# 下次第一条消息要重新加载 5GB 权重 + CUDA 预热，体感就是卡一下）。
# -1 = 永不卸载；也可设 "30m" / "1h" 等时长，空闲到时才释放。
LLM_KEEP_ALIVE = os.getenv("CAMPUS_LLM_KEEP_ALIVE", "-1")

# --------------------------------------------------------------------------
# 4. 检索配置（向量 + BM25 混合 + 重排）
# --------------------------------------------------------------------------
EMBED_MODEL = os.getenv("CAMPUS_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
RERANK_MODEL = os.getenv("CAMPUS_RERANK_MODEL", "BAAI/bge-reranker-base")
ENABLE_RERANK = os.getenv("CAMPUS_ENABLE_RERANK", "1") == "1"

VECTOR_TOP_K = int(os.getenv("CAMPUS_VECTOR_TOP_K", "8"))    # 向量召回条数
BM25_TOP_K = int(os.getenv("CAMPUS_BM25_TOP_K", "8"))        # BM25 召回条数
RRF_K = 60                                                   # RRF 融合平滑参数
RERANK_CANDIDATES = int(os.getenv("CAMPUS_RERANK_CANDIDATES", "12"))
# 最终喂给模型的片段数。
# 实测瓶颈不在生成（14 token/s）而在 prompt 处理（约 37 token/s），
# 每多喂一个 350 字片段就要多等约 9 秒，故从 4 压到 3。
RERANK_TOP_N = int(os.getenv("CAMPUS_RERANK_TOP_N", "3"))

# --------------------------------------------------------------------------
# 4.1 多助手路由
# --------------------------------------------------------------------------
# 路由分 = max(问题与助手描述的语义相似度, 问题与该助手所有片段质心的相似度)
# 阈值基于「检索证据路由」的实测分布标定（见 router.py）：
# 有效问题最高分落在 0.53~0.74，闲聊类问题普遍低于 0.42，故取 0.50/0.40 分档
ROUTE_ABS_THRESHOLD = float(os.getenv("CAMPUS_ROUTE_ABS", "0.50"))    # 达到则单选该助手
ROUTE_MARGIN = float(os.getenv("CAMPUS_ROUTE_MARGIN", "0.04"))        # 与第二名的最小差距
ROUTE_MAX_ASSISTANTS = int(os.getenv("CAMPUS_ROUTE_MAX", "2"))        # 最多协同几个助手
ROUTE_LOW_THRESHOLD = float(os.getenv("CAMPUS_ROUTE_LOW", "0.40"))    # 低于此值判为无法路由
ROUTE_USE_LLM_FALLBACK = os.getenv("CAMPUS_ROUTE_LLM", "0") == "1"    # 低置信度时用大模型兜底

# --------------------------------------------------------------------------
# 4.2 查询改写（LLM）
# --------------------------------------------------------------------------
# 实测：一次 LLM 往返在 CPU 上要 8~15 秒，所以不做无差别改写。
# 只对"短问题 / 含指代词 / 口语化"的问句触发，其余直接检索。
ENABLE_QUERY_REWRITE = os.getenv("CAMPUS_QUERY_REWRITE", "1") == "1"
REWRITE_MIN_LEN = int(os.getenv("CAMPUS_REWRITE_MIN_LEN", "12"))

# bge 系列官方建议：query 侧加指令前缀，doc 侧不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# 相似度阈值：低于该值的片段直接丢弃，避免"检索到一堆无关内容硬答"
MIN_SCORE = float(os.getenv("CAMPUS_MIN_SCORE", "0.30"))

# --------------------------------------------------------------------------
# 5. 文本切片
# --------------------------------------------------------------------------
CHUNK_SIZE = int(os.getenv("CAMPUS_CHUNK_SIZE", "350"))
CHUNK_OVERLAP = int(os.getenv("CAMPUS_CHUNK_OVERLAP", "50"))
MIN_CHUNK_SIZE = 60          # 小于此长度的片段并入相邻片段

# --------------------------------------------------------------------------
# 6. 会话
# --------------------------------------------------------------------------
MAX_HISTORY_TURNS = 2        # 保留最近 N 轮对话（CPU 环境上下文越少越快）
REQUEST_TIMEOUT = 600        # 单次生成超时（秒）——CPU 上长 prompt 可能超过 5 分钟

# --------------------------------------------------------------------------
# 7. 杂项
# --------------------------------------------------------------------------
IS_WINDOWS = sys.platform.startswith("win")
LOG_LEVEL = os.getenv("CAMPUS_LOG_LEVEL", "INFO")


def describe() -> str:
    """打印当前生效的关键配置，便于排查问题。"""
    lines = [
        "── 校园智能助手 配置 ──────────────────────",
        f"  对话模型   : {LLM_MODEL}  (think={LLM_THINK}, ctx={LLM_NUM_CTX})",
        f"  嵌入模型   : {EMBED_MODEL}",
        f"  重排模型   : {RERANK_MODEL if ENABLE_RERANK else '已关闭'}",
        f"  Ollama     : {OLLAMA_HOST}",
        f"  检索策略   : 向量Top{VECTOR_TOP_K} + BM25 Top{BM25_TOP_K} → RRF → "
        f"重排Top{RERANK_TOP_N}",
        f"  切片       : {CHUNK_SIZE} 字 / 重叠 {CHUNK_OVERLAP} 字",
        f"  文档目录   : {RAW_DIR}",
        "──────────────────────────────────────────",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
