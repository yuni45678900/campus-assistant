# -*- coding: utf-8 -*-
"""生成《混合检索融合流程》图：双路检索 -> 倒数秩融合 -> 重排 -> 生成。"""

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

mpl.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
mpl.rcParams["axes.unicode_minus"] = False

C_BLUE, C_BLUE_BG = "#2B6CB0", "#EBF4FF"
C_TEAL, C_TEAL_BG = "#0F6E56", "#E6F7F1"
C_ORANGE, C_ORANGE_BG = "#B45309", "#FEF3E2"
C_GRAY, C_GRAY_BG = "#4A5568", "#F7FAFC"
C_DARK = "#1A202C"
C_LINE = "#4A5568"

fig, ax = plt.subplots(figsize=(11, 9.2), dpi=200)
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")


def box(x, y, w, h, title, lines, edge, bg, title_size=12.5, body_size=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2",
                                linewidth=1.6, edgecolor=edge, facecolor=bg, zorder=3))
    ty = y + h - 3.2 if lines else y + h / 2
    ax.text(x + w / 2, ty, title, ha="center", va="center", fontsize=title_size,
            fontweight="bold", color=edge, zorder=4)
    for i, ln in enumerate(lines):
        ax.text(x + w / 2, ty - 3.6 - i * 3.0, ln, ha="center", va="center",
                fontsize=body_size, color=C_DARK, zorder=4)


def arrow(x1, y1, x2, y2, style="-|>", lw=1.5, color=C_LINE):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=13, linewidth=lw, color=color, zorder=2))


ax.text(50, 96.5, "混合检索融合流程", fontsize=17, fontweight="bold",
        ha="center", va="top", color=C_DARK)
ax.text(50, 92.3, "双路检索各自排序，按名次融合后精排取前三，再交给本地模型作答",
        fontsize=10.5, ha="center", va="top", color="#4A5568")

# 1 用户问题
box(34, 79, 32, 9, "用户提问", ["一句自然语言问题"], C_GRAY, C_GRAY_BG)
arrow(50, 79, 50, 74.5)

# 2 分类路由
box(34, 65, 32, 9, "分类路由", ["按问题语义确定检索范围"], C_BLUE, C_BLUE_BG)

# 分叉
ax.plot([50, 50], [65, 61.5], color=C_LINE, lw=1.5, zorder=2)
ax.plot([22, 78], [61.5, 61.5], color=C_LINE, lw=1.5, zorder=2)
arrow(22, 61.5, 22, 57.5)
arrow(78, 61.5, 78, 57.5)

# 3 双路
box(4, 43, 36, 14, "向量检索", ["语义相近的片段", "取前 8 条"], C_TEAL, C_TEAL_BG)
box(60, 43, 36, 14, "关键词检索 BM25", ["字面精确匹配的片段", "取前 8 条"], C_TEAL, C_TEAL_BG)

# 汇合
arrow(22, 43, 22, 39.5)
arrow(78, 43, 78, 39.5)
ax.plot([22, 78], [39.5, 39.5], color=C_LINE, lw=1.5, zorder=2)
arrow(50, 39.5, 50, 35.5)

# 4 融合
box(24, 20, 52, 15, "倒数秩融合", ["两路名次分别取倒数后相加", "两路都靠前的片段排到最前"], C_ORANGE, C_ORANGE_BG)

# 5 重排
arrow(50, 20, 50, 16.5)
box(18, 6, 50, 10, "重排取前三", ["重排模型对融合结果精排，保留 Top 3"], C_ORANGE, C_ORANGE_BG)

# 6 生成（放右侧下方）
box(71.5, 6, 27, 12.5, "本地模型生成", ["拼入提示词后作答", "答复标注来源文件"], C_BLUE, C_BLUE_BG, title_size=11.5, body_size=9.5)
arrow(68, 11.5, 71.5, 11.5)

# 左侧说明：为什么按名次
ax.text(3, 33.5, "为什么不直接加分", fontsize=10.5, fontweight="bold", color=C_GRAY, va="top")
for i, t in enumerate(["向量给的是相似度，", "关键词给的是词频分，", "两者量纲不同、不可比；", "名次却可以比。"]):
    ax.text(3, 29.6 - i * 3.1, t, fontsize=9.5, color=C_DARK, va="top")

plt.tight_layout(pad=0.4)
fig.savefig(r"D:\campus-assistant\docs\融合流程图.png", dpi=200,
            bbox_inches="tight", facecolor="white")
print("OK")
