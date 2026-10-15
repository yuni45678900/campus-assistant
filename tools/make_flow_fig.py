# -*- coding: utf-8 -*-
"""生成《校园小助手》核心功能流程图：管理端建库流程 + 学生端问答流程。

与 tools/make_architecture_fig.py 保持同一套配色与画法。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

W, H = 13.2, 7.6
fig = plt.figure(figsize=(W, H), dpi=200)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 100)
ax.set_ylim(9, 100)
ax.axis("off")

C_BUILD = "#DCE9F7"   # 建库流程（蓝）
C_ASK = "#FBE7D5"     # 问答流程（橙）
EDGE = "#4A5568"
BLUE = "#2B6CB0"

NW, NH = 13.6, 13.0
XS = [1.0 + i * 16.3 for i in range(6)]   # 6 个节点的左边界


def node(x, y, title, subs, fill):
    ax.add_patch(FancyBboxPatch((x, y), NW, NH,
                                boxstyle="round,pad=0,rounding_size=1.0",
                                linewidth=1.3, edgecolor=EDGE, facecolor=fill, zorder=2))
    ax.text(x + NW / 2, y + NH - 2.4, title, fontsize=10.8, fontweight="bold",
            ha="center", va="center", color="#1A202C", zorder=3)
    for i, s in enumerate(subs):
        ax.text(x + NW / 2, y + NH - 5.0 - i * 2.5, s, fontsize=8.3,
                ha="center", va="center", color="#4A5568", zorder=3)


def arrow(x1, x2, y):
    ax.add_patch(FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>",
                                 mutation_scale=14, linewidth=1.5, color=BLUE, zorder=4))


ax.text(50, 97.4, "校园小助手 · 核心功能流程", fontsize=16.5, fontweight="bold",
        ha="center", va="top", color="#1A202C")
ax.text(50, 93.2, "两条流程各自构成“输入 → 处理 → 输出”的完整闭环，两端共用同一套校本知识库",
        fontsize=10.2, ha="center", va="top", color="#4A5568")

# ---------------- 建库流程 ----------------
ax.text(1.0, 87.0, "管理端 · 建库流程", fontsize=12, fontweight="bold",
        ha="left", va="top", color="#2A4365")
ax.text(24.5, 87.0, "输入：校本资料　　输出：可直接问答的校本知识库",
        fontsize=9.6, ha="left", va="top", color="#4A5568")

YB = 63.0
build = [
    ("校本资料", [".docx / .pdf", ".md / .txt"]),
    ("批量上传", ["管理端统一上传", "单文件上限 1024 MB"]),
    ("分类判定", ["文件夹名优先", "未指定→本地模型", "失败→降级文件名"]),
    ("解析与切片", ["解析正文含表格", "清洗后中文语义", "切片 350字/重叠50"]),
    ("双路索引", ["向量索引", "+ BM25 关键词索引"]),
    ("分库落盘", ["按分类分库存储", "支持一键重建"]),
]
for i, (t, s) in enumerate(build):
    node(XS[i], YB, t, s, C_BUILD)
    if i:
        arrow(XS[i - 1] + NW, XS[i] - 1.6, YB + NH / 2)

# ---------------- 问答流程 ----------------
ax.text(1.0, 51.5, "学生端 · 问答流程", fontsize=12, fontweight="bold",
        ha="left", va="top", color="#7B341E")
ax.text(24.5, 51.5, "输入：自然语言问题　　输出：带来源标注的答复",
        fontsize=9.6, ha="left", va="top", color="#4A5568")

YA = 27.0
ask = [
    ("自然语言提问", ["学生登录后直接问", "不列出资料分类"]),
    ("分类路由", ["按问题语义路由到", "最相关的分类"]),
    ("双路召回", ["向量 Top8", "+ BM25 Top8"]),
    ("RRF 融合与重排", ["RRF 融合后", "重排取 Top3"]),
    ("本地模型生成", ["Qwen3-8B 本机推理", "控制在 300 字内"]),
    ("标注来源·流式返回", ["末尾标明文件名", "显示来源与章节", "SSE 逐字返回"]),
]
for i, (t, s) in enumerate(ask):
    node(XS[i], YA, t, s, C_ASK)
    if i:
        arrow(XS[i - 1] + NW, XS[i] - 1.6, YA + NH / 2)

# 兜底说明：检索未命中时（挂在「RRF 融合与重排」即第 4 个节点下方）
ax.add_patch(FancyBboxPatch((XS[3], YA - 14.2), NW, 8.6,
                            boxstyle="round,pad=0,rounding_size=1.0",
                            linewidth=1.2, edgecolor="#A0AEC0", facecolor="#F2F2F2", zorder=2))
ax.text(XS[3] + NW / 2, YA - 9.6, "未命中相关资料时", fontsize=9.0, ha="center", va="center",
        color="#2D3748", zorder=3)
ax.text(XS[3] + NW / 2, YA - 12.4, "明确回复“现有资料\n未包含该信息”", fontsize=8.3,
        ha="center", va="center", color="#4A5568", zorder=3)
ax.add_patch(FancyArrowPatch((XS[3] + NW / 2, YA), (XS[3] + NW / 2, YA - 5.6),
                             arrowstyle="-|>", mutation_scale=12, linewidth=1.3,
                             color="#A0AEC0", zorder=4))

fig.savefig(r"D:\campus-assistant\docs\核心功能流程图.png", dpi=200,
            facecolor="white", bbox_inches="tight", pad_inches=0.22)
print("OK")
