# -*- coding: utf-8 -*-
"""生成《校园小助手》系统架构图（分层架构 + 建库/问答两条流程）。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

W, H = 12.4, 9.6
fig = plt.figure(figsize=(W, H), dpi=200)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")

C_BROWSER = "#DCE9F7"
C_SERVICE = "#D9EFE4"
C_AI = "#FBE7D5"
C_DATA = "#E6E0F2"
C_FLOW = "#F2F2F2"
EDGE = "#4A5568"


def box(x, y, w, h, title, lines, fill, title_size=13.5, body_size=11.2, radius=1.2):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            linewidth=1.4, edgecolor=EDGE, facecolor=fill, zorder=2,
        )
    )
    ax.text(x + 1.6, y + h - 2.2, title, fontsize=title_size, fontweight="bold",
            va="top", ha="left", color="#1A202C", zorder=3)
    for i, ln in enumerate(lines):
        ax.text(x + 1.6, y + h - 5.0 - i * 2.55, ln, fontsize=body_size,
                va="top", ha="left", color="#2D3748", zorder=3, linespacing=1.35)


def arrow(x1, y1, x2, y2, label="", dx=0.0, fs=10.5, style="-|>"):
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1), (x2, y2), arrowstyle=style, mutation_scale=16,
            linewidth=1.5, color="#2B6CB0", zorder=4,
        )
    )
    if label:
        ax.text((x1 + x2) / 2 + dx, (y1 + y2) / 2, label, fontsize=fs,
                ha="center", va="center", color="#2B6CB0", zorder=5,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none"))


ax.text(50, 97.6, "系统架构", fontsize=17, fontweight="bold",
        ha="center", va="top", color="#1A202C")
ax.text(50, 93.6, "分层结构 · 全部组件在本机运行，运行期不调用任何外部接口",
        fontsize=11, ha="center", va="top", color="#4A5568")

LX, LW = 3.0, 94.0

# ---- 浏览器端 ----
box(LX, 76.0, LW, 13.6, "浏览器端",
    ["学生端 / 管理端 两个入口页面",
     "原生 HTML + CSS + JavaScript 实现，不引入前端框架",
     "学生端不列出资料分类；管理端含概览 / 分类 / 上传 / 账号 / 设置 五个视图"],
    C_BROWSER)

# ---- 服务层 ----
box(LX, 55.5, LW, 13.4, "服务层（FastAPI + Uvicorn）",
    ["会话鉴权：角色、账号、签发时间与 HMAC 签名的 Cookie，每次请求回查账号表",
     "静态资源托管 ｜ SSE 流式输出（前端同步显示“检索中 / 生成中”）",
     "上传与管理接口：标准 multipart + 零依赖裸流兜底通道，单文件上限可配置"],
    C_SERVICE)

# ---- AI 服务层 ----
box(LX, 27.0, LW, 21.6, "AI 服务层（本机推理）",
    ["分类路由：分类由资料目录动态发现，零硬编码；模型不可用时逐级退让",
     "混合检索：向量 Top8 + BM25 Top8 → 倒数秩融合 → 重排",
     "本地对话模型 Qwen3-8B（Ollama 本地推理；有独显自动载入显存，无独显回落 CPU）",
     "提示词约束：仅依据命中的校本资料作答，资料不足时如实说明，并强制标注来源",
     "语料解析与中文友好切片：按标题层级切分并保留标题路径，Word 表格转为键值对文本"],
    C_AI)

# ---- 数据层 ----
box(LX, 10.0, LW, 12.0, "数据层（按分类分库）",
    ["知识库索引：chunks.json（片段与来源）｜ embeddings.npy（向量）｜ bm25.pkl（关键词）",
     "账号库 accounts.json ｜ 站点配置 settings.json ｜ 回收站（删除采用软删除）"],
    C_DATA)

# ---- 层间箭头 ----
arrow(50, 76.0, 50, 69.3, "提问 / 上传", dx=7.5)
arrow(50, 68.9, 50, 76.0 - 0.4, "", style="-|>")
ax.add_patch(FancyArrowPatch((43, 69.1), (43, 75.7), arrowstyle="-|>",
                             mutation_scale=16, linewidth=1.5, color="#C05621", zorder=4))
ax.text(43, 72.4, "流式答复", fontsize=10.5, ha="center", va="center", color="#C05621",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none"), zorder=5)

arrow(50, 55.5, 50, 48.9, "提问", dx=5.0)
ax.add_patch(FancyArrowPatch((43, 49.0), (43, 55.3), arrowstyle="-|>",
                             mutation_scale=16, linewidth=1.5, color="#C05621", zorder=4))
ax.text(43, 52.1, "命中的资料片段", fontsize=10.5, ha="center", va="center", color="#C05621",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none"), zorder=5)

arrow(50, 27.0, 50, 22.3, "索引读写", dx=6.5)

# ---- 两条流程 ----
ax.add_patch(FancyBboxPatch((LX, 1.6), LW, 6.4,
                            boxstyle="round,pad=0,rounding_size=1.2",
                            linewidth=1.2, edgecolor="#A0AEC0", facecolor=C_FLOW, zorder=2))
ax.text(LX + 1.6, 6.9, "建库流程", fontsize=11.5, fontweight="bold", va="top", ha="left",
        color="#2D3748", zorder=3)
ax.text(LX + 12.5, 6.9,
        "校本资料（.docx / .pdf / .md / .txt）  →  解析（含 Word 表格）  →  文本清洗  →  中文切片  →  向量与关键词双路索引",
        fontsize=10.6, va="top", ha="left", color="#2D3748", zorder=3)
ax.text(LX + 1.6, 3.5, "问答流程", fontsize=11.5, fontweight="bold", va="top", ha="left",
        color="#2D3748", zorder=3)
ax.text(LX + 12.5, 3.5,
        "用户提问  →  分类路由  →  双路检索  →  倒数秩融合  →  重排  →  本地生成  →  标注来源文件与章节（SSE 流式返回）",
        fontsize=10.6, va="top", ha="left", color="#2D3748", zorder=3)

fig.savefig(r"D:\campus-assistant\docs\系统架构图.png", dpi=200,
            facecolor="white", bbox_inches="tight", pad_inches=0.22)
print("OK")
