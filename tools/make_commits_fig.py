# -*- coding: utf-8 -*-
"""生成代码仓库提交记录图：数据来自 git log，不做任何修饰。"""

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle

mpl.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
mpl.rcParams["axes.unicode_minus"] = False

# 数据源：git log --format="%h|%s|%ad"（时间从旧到新排列）
COMMITS = [
    ("c2dd58c", "feat", "校园智能小助手：基于本地大模型与混合检索 RAG 的校园智能体生成框架", "10-15 14:31"),
    ("94fdfba", "chore", "示例地址改为中性占位符，去除个人仓库链接", "10-15 14:32"),
    ("40fbe77", "docs", "补充开源说明（公开范围、排除项与安全自查结论）", "10-15 14:32"),
    ("8de356e", "chore", "忽略本地开源推送脚本", "10-15 14:39"),
    ("40b3945", "fix", "寒暄类提问不再走检索；新增核心功能流程图", "10-15 15:54"),
    ("2547488", "docs", "移除过时的静态展示站说明，修正框架名与硬件表", "10-15 16:11"),
    ("024e32e", "fix", "订正管理端上传说明里的文件上限（50 MB → 1024 MB）", "10-15 18:57"),
    ("b4368bc", "docs", "补充技术报告用的界面截图（门户 / 学生端 / 管理端）", "10-15 18:57"),
]

COLOR = {
    "feat": ("#2B6CB0", "#EBF4FF"),
    "fix": ("#B45309", "#FEF3E2"),
    "docs": ("#0F6E56", "#E6F7F1"),
    "chore": ("#5F5E5A", "#F1EFE8"),
}

N = len(COMMITS)
fig, ax = plt.subplots(figsize=(10.4, 4.3), dpi=200)
ax.set_xlim(0, 100)
ax.set_ylim(6, 100)
ax.axis("off")

TOP, GAP = 95.0, 11.4
LINE_X = 9.5

# 竖线（时间轴）
ax.plot([LINE_X, LINE_X], [TOP + 2.2, TOP - GAP * (N - 1) - 2.2],
        color="#CBD5E0", linewidth=2.0, zorder=1, solid_capstyle="round")

for i, (sha, kind, msg, when) in enumerate(COMMITS):
    y = TOP - GAP * i
    edge, bg = COLOR[kind]
    ax.add_patch(Circle((LINE_X, y), 0.95, facecolor=edge, edgecolor="white",
                        linewidth=1.6, zorder=4))
    # 提交号 + 时间（轴左侧）
    ax.text(LINE_X - 1.9, y + 0.6, sha, fontsize=8.6, ha="right", va="center",
            color="#2D3748", family="monospace")
    ax.text(LINE_X - 1.9, y - 1.9, when, fontsize=7.8, ha="right", va="center",
            color="#718096")
    # 类型徽标
    ax.add_patch(FancyBboxPatch((LINE_X + 3.0, y - 1.15), 7.2, 2.5,
                                boxstyle="round,pad=0.12,rounding_size=0.5",
                                facecolor=bg, edgecolor=edge, linewidth=0.9, zorder=3))
    ax.text(LINE_X + 6.6, y + 0.1, kind, fontsize=8.0, ha="center", va="center",
            color=edge, fontweight="bold", zorder=4)
    # 提交说明
    ax.text(LINE_X + 11.6, y, msg, fontsize=9.8, ha="left", va="center", color="#1A202C")

fig.savefig(r"D:\campus-assistant\docs\提交记录图.png", dpi=200,
            bbox_inches="tight", pad_inches=0.28, facecolor="white")
print("OK")
