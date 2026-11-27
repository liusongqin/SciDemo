#!/usr/bin/env python3
"""Build the editable five-slide deck from the previously written Markdown outline."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt


HERE = Path(__file__).resolve().parent
OUT = HERE.parent
OUTLINE = OUT / "SciDemo_Agent入门介绍_逐页讲稿.md"
PPTX = OUT / "SciDemo_Agent入门介绍.pptx"
ASSETS = HERE / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
FONT = "Noto Sans CJK SC"
W, H = 13.333333, 7.5

C = {
    "bg": "F7F7F2", "paper": "FFFFFF", "ink": "172F43", "muted": "61737D",
    "line": "DCE5E4", "teal": "087F80", "mint": "A8E4D5", "teal_bg": "E4F3EF",
    "blue": "5166BD", "blue_bg": "EBEEFB", "orange": "C46D42", "orange_bg": "FBEDE2",
    "night": "142C40", "night_card": "203E53", "night_muted": "BED1DA",
    "night_line": "426173", "cream": "F4F3E9",
}


def rgb(key: str) -> RGBColor:
    return RGBColor.from_string(C.get(key, key))


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)
prs.core_properties.title = "SciDemo：会使用计算工具的学习助手"
prs.core_properties.subject = "面向零基础理科学生的 Agent 原理与项目架构介绍"
prs.core_properties.author = "SciDemo"
prs.core_properties.keywords = "Agent,科学计算,入门,教学,系统架构"
outline = OUTLINE.read_text(encoding="utf-8")
prs.core_properties.comments = "依据先完成的逐页讲稿制作；讲稿 SHA256=" + hashlib.sha256(OUTLINE.read_bytes()).hexdigest()


def shape(slide, x, y, w, h, fill, border=None, radius=False):
    obj = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
                                 Inches(x), Inches(y), Inches(w), Inches(h))
    obj.fill.solid()
    obj.fill.fore_color.rgb = rgb(fill)
    if border:
        obj.line.color.rgb = rgb(border)
        obj.line.width = Pt(1)
    else:
        obj.line.fill.background()
    if radius:
        obj.adjustments[0] = 0.10
    return obj


def text(slide, content, x, y, w, h, size=18, color="ink", bold=False,
         align=PP_ALIGN.LEFT, vertical=MSO_ANCHOR.TOP, spacing=1.15):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = vertical
    for n, line in enumerate(content.split("\n")):
        p = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
        p.alignment = align
        p.space_before = p.space_after = Pt(0)
        p.line_spacing = spacing
        run = p.add_run()
        run.text = line
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
        prop = run._r.get_or_add_rPr()
        prop.set("lang", "zh-CN")
        for name in ("a:ea", "a:cs"):
            child = OxmlElement(name)
            child.set("typeface", FONT)
            prop.append(child)
    return box


def line(slide, x1, y1, x2, y2, color="line", width=1.5, arrow=False, both=False):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = rgb(color)
    conn.line.width = Pt(width)
    if arrow:
        end = OxmlElement("a:tailEnd")
        end.set("type", "triangle")
        end.set("w", "sm")
        end.set("len", "sm")
        conn.line._get_or_add_ln().append(end)
    if both:
        start = OxmlElement("a:headEnd")
        start.set("type", "triangle")
        start.set("w", "sm")
        start.set("len", "sm")
        conn.line._get_or_add_ln().append(start)
    return conn


def circle(slide, x, y, diameter, fill, label="", size=16, color="paper"):
    obj = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(diameter), Inches(diameter))
    obj.fill.solid()
    obj.fill.fore_color.rgb = rgb(fill)
    obj.line.fill.background()
    if label:
        text(slide, label, x, y + 0.005, diameter, diameter - 0.005, size=size, bold=True,
             color=color, align=PP_ALIGN.CENTER, vertical=MSO_ANCHOR.MIDDLE)
    return obj


def footer(slide, number, dark=False, note=None):
    lc = "night_line" if dark else "line"
    tc = "night_muted" if dark else "muted"
    line(slide, 0.66, 7.02, 12.67, 7.02, lc, 0.7)
    text(slide, note or "SCIDEMO  /  AGENT 入门", 0.68, 7.12, 10.9, 0.19, 9.5, tc)
    text(slide, f"{number:02d} / 05", 11.80, 7.10, 0.85, 0.23, 10.5, tc, align=PP_ALIGN.RIGHT)


def base(number, topic, title, subtitle="", dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb("night" if dark else "bg")
    text(slide, f"SCIDEMO  /  {topic}", 0.68, 0.38, 11.8, 0.25, 10.5,
         "mint" if dark else "teal", bold=True)
    text(slide, title, 0.68, 0.93, 12.0, 0.65, 31,
         "paper" if dark else "ink", bold=True)
    if subtitle:
        text(slide, subtitle, 0.70, 1.65, 11.95, 0.44, 17,
             "night_muted" if dark else "muted")
    footer(slide, number, dark)
    return slide


def add_notes(slide, number):
    found = re.search(rf"## 第 {number} 页[｜|].*?(?=\n## 第 \d 页|\n## 内容核验|\Z)", outline, re.S)
    assert found, f"Missing page {number} in Markdown"
    page = found.group(0)
    speech = re.search(r"### 建议讲解稿\n(.*?)(?=\n### |\Z)", page, re.S).group(1).strip()
    takeaway = re.search(r"### 学生带走的一句话\n(.*?)(?=\n### |\Z)", page, re.S).group(1).strip()
    notes = f"第 {number} 页\n\n讲解稿：\n{speech}\n\n本页结论：\n{takeaway.replace('**', '')}"
    if number == 3:
        notes += "\n\n术语：Problem Analyst、Scientific Solver、Verification Critic、Report Writer。四角色可以共用模型。"
    if number == 4:
        notes += ("\n\n实际工具输出：find_root(expression='x^2-2', method='brentq', bracket=[1,2], tolerance=1e-10)。"
                  "root=1.4142135623731364，residual=1.1723955140041653e-13。"
                  "工具输出用于教学示例，曲线为数学示意，不是一次完整 Agent 对话的截图。")
    slide.notes_slide.notes_text_frame.text = notes


def create_plot():
    plt.rcParams.update({"font.family": FONT, "axes.unicode_minus": False,
                         "font.size": 16, "axes.labelsize": 17})
    fig, ax = plt.subplots(figsize=(7.0, 4.5), dpi=230)
    fig.patch.set_facecolor("white")
    xs = np.linspace(0, 2.05, 401)
    ax.plot(xs, xs**2 - 2, color="#087F80", linewidth=3.2, zorder=3)
    ax.axhline(0, color="#61737D", linewidth=1.3, zorder=1)
    ax.axvline(0, color="#61737D", linewidth=1.3, zorder=1)
    root = math.sqrt(2)
    ax.plot([root, root], [-2.35, 0], color="#C46D42", linewidth=1.3, linestyle=(0, (4, 4)))
    ax.scatter([root], [0], s=95, color="#C46D42", edgecolors="white", linewidths=1.4, zorder=5)
    ax.annotate("正数解", xy=(root, 0), xytext=(1.01, 1.15), color="#172F43", fontsize=18,
                arrowprops={"arrowstyle": "->", "color": "#C46D42", "lw": 1.6})
    ax.text(0.10, 1.65, "y = x² − 2", color="#087F80", fontsize=21)
    ax.text(root + 0.08, -0.55, "x ≈ 1.4142", color="#C46D42", fontsize=15)
    ax.set_xlim(-0.05, 2.08)
    ax.set_ylim(-2.35, 2.4)
    ax.set_xticks([0, 0.5, 1, 1.5, 2])
    ax.set_yticks([-2, -1, 0, 1, 2])
    ax.tick_params(axis="both", length=0, colors="#61737D", labelsize=13, pad=8)
    ax.set_xlabel("x", loc="right", color="#61737D", labelpad=0)
    ax.set_ylabel("y", rotation=0, loc="top", color="#61737D", labelpad=0)
    ax.grid(color="#E9EFEE", linewidth=0.8)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.subplots_adjust(left=0.10, right=0.97, top=0.94, bottom=0.14)
    path = ASSETS / "root_curve.png"
    fig.savefig(path, dpi=230, facecolor="white")
    plt.close(fig)
    return path


# 1. Familiar question and a usable definition of Agent.
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid()
s.background.fill.fore_color.rgb = rgb("night")
text(s, "SCIDEMO  /  科学计算 AGENT", 0.72, 0.48, 11.7, 0.28, 12, "mint", bold=True)
text(s, "会使用计算工具的\n学习助手", 0.70, 1.13, 7.3, 1.63, 43, "paper", bold=True, spacing=1.12)
text(s, "围绕目标选择工具、读取结果，\n再决定下一步的 AI 助手。", 0.76, 2.99, 7.1, 0.9, 20, "night_muted", spacing=1.3)
shape(s, 0.74, 4.25, 7.18, 1.15, "night_card", radius=True)
text(s, "面积为 2 的正方形，边长是多少？", 0.99, 4.47, 6.70, 0.39, 20, "paper", bold=True)
text(s, "请说明怎么算、怎么检查。", 0.99, 4.96, 6.65, 0.26, 15.5, "night_muted")
for x, label in ((0.76, "计算结果"), (3.12, "检查依据"), (5.48, "解题说明")):
    shape(s, x, 5.93, 2.10, 0.51, "night_card", radius=True)
    circle(s, x + 0.15, 6.09, 0.13, "mint")
    text(s, label, x + 0.42, 6.04, 1.55, 0.28, 16, "paper")
square_x, square_y, side = 9.18, 2.03, 2.47
shape(s, square_x, square_y, side, side, "night_card", "mint")
text(s, "面积", square_x, 2.52, side, 0.32, 17, "night_muted", align=PP_ALIGN.CENTER)
text(s, "2", square_x, 2.98, side, 0.99, 61, "mint", bold=True, align=PP_ALIGN.CENTER)
line(s, 8.86, square_y, 8.86, square_y + side, "mint", 1.2)
line(s, 8.76, square_y, 8.96, square_y, "mint", 1.2)
line(s, 8.76, square_y + side, 8.96, square_y + side, "mint", 1.2)
text(s, "x", 8.36, 3.13, 0.32, 0.42, 23, "mint", bold=True)
line(s, square_x, 4.79, square_x + side, 4.79, "mint", 1.2)
line(s, square_x, 4.69, square_x, 4.89, "mint", 1.2)
line(s, square_x + side, 4.69, square_x + side, 4.89, "mint", 1.2)
text(s, "x", 10.20, 4.91, 0.40, 0.34, 23, "mint", bold=True, align=PP_ALIGN.CENTER)
text(s, "x² = 2，x > 0", 8.85, 5.64, 3.25, 0.45, 23, "paper", align=PP_ALIGN.CENTER)
footer(s, 1, True)
add_notes(s, 1)

# 2. A loop, rather than a script of opaque answers.
s = base(2, "工作原理", "一次求解，是一个反馈循环", "像做实验一样：先选方法，再看结果，根据证据决定下一步。")
cards = [
    ("理解目标", "题目给了什么？\n最后要得到什么？", "blue", "blue_bg"),
    ("选择工具", "求根、求导、积分，\n还是画图？", "teal", "teal_bg"),
    ("执行计算", "科学计算程序\n实际完成运算", "teal", "teal_bg"),
    ("检查后决定", "结果是否可靠？\n还需要做什么？", "orange", "orange_bg"),
]
xs = [0.70, 3.78, 6.86, 9.94]
for i, (title, desc, accent, tint) in enumerate(cards):
    x = xs[i]
    shape(s, x, 2.39, 2.70, 2.02, "paper", "line", radius=True)
    circle(s, x + 0.21, 2.62, 0.43, accent, str(i + 1), 16)
    text(s, title, x + 0.22, 3.22, 2.29, 0.43, 21, "ink", bold=True)
    text(s, desc, x + 0.23, 3.68, 2.27, 0.60, 15.3, "muted", spacing=1.22)
    if i < 3:
        line(s, x + 2.76, 3.37, xs[i + 1] - 0.09, 3.37, "muted", 1.7, arrow=True)
line(s, 11.29, 4.43, 11.29, 5.11, "orange", 2.0)
line(s, 11.29, 5.11, 5.13, 5.11, "orange", 2.0)
line(s, 5.13, 5.11, 5.13, 4.47, "orange", 2.0, arrow=True)
shape(s, 5.57, 4.84, 5.17, 0.53, "bg")
text(s, "未通过：调整方法或参数，再尝试", 5.66, 4.97, 4.98, 0.31, 16.5, "orange", bold=True, align=PP_ALIGN.CENTER)
line(s, 12.64, 3.68, 12.82, 3.68, "teal", 1.8)
line(s, 12.82, 3.68, 12.82, 5.55, "teal", 1.8)
line(s, 12.82, 5.55, 10.55, 5.55, "teal", 1.8, arrow=True)
text(s, "可汇总时：说明与证据", 7.25, 5.39, 3.13, 0.32, 16, "teal", bold=True)
shape(s, 0.72, 6.00, 5.75, 0.56, "blue_bg", radius=True)
text(s, "语言模型：理解目标、选择下一步", 0.93, 6.12, 5.34, 0.31, 17, "blue", bold=True)
shape(s, 6.77, 6.00, 5.85, 0.56, "teal_bg", radius=True)
text(s, "计算工具：执行运算、返回结果", 6.98, 6.12, 5.41, 0.31, 17, "teal", bold=True)
text(s, "尝试有次数与时间限制；必要时可由人确认。", 0.76, 6.72, 11.8, 0.22, 11, "muted")
add_notes(s, 2)

# 3. Four roles, with one explicit responsibility per card.
s = base(3, "多 AGENT 协作", "像一组同学，各自负责一件事", "同一道题按职责分工，让求解与检查的过程更清楚。")
roles = [
    ("问题分析员", "读题", "读懂条件与目标", "面积为 2，\n求正的边长", "blue", "blue_bg"),
    ("科学求解员", "求解", "选择方法，调用工具", "调用求根工具，\n求解 x²−2=0", "teal", "teal_bg"),
    ("验证审查员", "检查", "程序验算 + 模型复核", "代回原式，\n检查差值是否够小", "orange", "orange_bg"),
    ("报告撰写员", "讲解", "根据已有证据解释", "说明结果、方法\n与检查依据", "blue", "blue_bg"),
]
for i, (title, verb, desc, example, accent, tint) in enumerate(roles):
    x = xs[i]
    shape(s, x, 2.37, 2.70, 3.17, "paper", "line", radius=True)
    text(s, f"0{i + 1}   {title}", x + 0.20, 2.64, 2.32, 0.31, 15.5, accent, bold=True)
    text(s, verb, x + 0.20, 3.13, 2.30, 0.61, 34, "ink", bold=True)
    text(s, desc, x + 0.20, 3.95, 2.34, 0.33, 14.2, "muted")
    shape(s, x + 0.17, 4.47, 2.36, 0.83, tint, radius=True)
    text(s, example, x + 0.31, 4.62, 2.10, 0.59, 15.1, accent, spacing=1.18)
    if i < 3:
        line(s, x + 2.76, 3.53, xs[i + 1] - 0.09, 3.53, "muted", 1.7, arrow=True)
line(s, 8.21, 5.56, 8.21, 5.95, "orange", 1.8)
line(s, 8.21, 5.95, 5.13, 5.95, "orange", 1.8)
line(s, 5.13, 5.95, 5.13, 5.58, "orange", 1.8, arrow=True)
shape(s, 5.34, 5.72, 2.69, 0.49, "bg")
text(s, "未通过，返回求解员", 5.36, 5.85, 2.65, 0.28, 14.1, "orange", bold=True, align=PP_ALIGN.CENTER)
shape(s, 0.72, 6.40, 11.9, 0.47, "teal_bg", radius=True)
text(s, "四种角色可以共用同一个模型；分开的是职责。", 1.04, 6.49, 11.25, 0.30, 18, "teal", bold=True, align=PP_ALIGN.CENTER)
# Replace the generic footer text with the concrete verification rule.
for obj in s.shapes:
    if obj.has_text_frame and obj.text == "SCIDEMO  /  AGENT 入门":
        obj.text_frame.clear()
        obj._element.getparent().remove(obj._element)
text(s, "程序检查失败，模型不能将其改为通过。", 0.70, 7.12, 10.8, 0.20, 9.5, "muted")
add_notes(s, 3)

# 4. Concrete computation and an exported scientific plot.
plot = create_plot()
s = base(4, "一个具体例子", "算出答案以后，还要代回去检查", "从面积为 2 的正方形出发，寻找正的边长。")
shape(s, 0.72, 2.20, 5.33, 0.63, "teal_bg", radius=True)
text(s, "x² − 2 = 0，x > 0", 1.00, 2.32, 4.80, 0.39, 25, "teal", bold=True)
steps = [
    ("设定任务", "在 1 到 2 之间寻找正数解。"),
    ("工具求根", "得到近似值 x ≈ 1.41421356。"),
    ("程序验算", "检查 |x²−2| 是否足够接近 0。"),
    ("复核与解释", "模型审阅证据，再组织答案。"),
]
for i, (title, desc) in enumerate(steps):
    y = 3.02 + i * 0.70
    circle(s, 0.80, y, 0.34, "teal", str(i + 1), 12)
    text(s, title, 1.31, y - 0.015, 4.67, 0.31, 17.0, "ink", bold=True)
    text(s, desc, 1.31, y + 0.32, 4.74, 0.29, 14.7, "muted")
shape(s, 6.48, 2.18, 6.14, 3.59, "paper", "line", radius=True)
s.shapes.add_picture(str(plot), Inches(6.58), Inches(2.23), width=Inches(5.90), height=Inches(3.47))
shape(s, 0.74, 6.06, 5.72, 0.66, "teal_bg", radius=True)
text(s, "边长 ≈ 1.41421356", 0.99, 6.20, 5.21, 0.38, 24, "teal", bold=True)
shape(s, 6.73, 6.06, 5.88, 0.66, "blue_bg", radius=True)
text(s, "差值 ≈ 1.17 × 10⁻¹³ < 10⁻⁸", 6.94, 6.24, 5.47, 0.34, 20, "blue", bold=True)
for obj in list(s.shapes):
    if obj.has_text_frame and obj.text == "SCIDEMO  /  AGENT 入门":
        obj._element.getparent().remove(obj._element)
text(s, "验算使用完整精度的工具结果；曲线为数学示意。数值检查提供证据，不等于完整数学证明。", 0.70, 7.10, 11.0, 0.22, 8.9, "muted")
add_notes(s, 4)

# 5. The complete architecture, explained in terms of jobs rather than APIs.
s = base(5, "系统架构", "网页背后，谁在做什么？", "先看清各部分的分工，再理解它们怎样配合。", True)
shape(s, 0.78, 2.29, 3.33, 1.36, "night_card", "night_line", radius=True)
text(s, "网页界面", 1.02, 2.51, 2.85, 0.42, 25, "paper", bold=True)
text(s, "提问 · 看结果 · 参与审核", 1.03, 3.02, 2.88, 0.30, 14.1, "night_muted")
text(s, "Vue", 1.03, 3.39, 2.70, 0.18, 10, "mint")
line(s, 4.25, 2.98, 5.07, 2.98, "mint", 2.0, arrow=True, both=True)
text(s, "问题", 4.18, 2.57, 0.96, 0.23, 11.5, "night_muted", align=PP_ALIGN.CENTER)
text(s, "进度与结果", 4.18, 3.16, 0.96, 0.23, 9.6, "night_muted", align=PP_ALIGN.CENTER)
shape(s, 5.23, 2.29, 7.30, 1.36, "teal", radius=True)
text(s, "流程控制中心", 5.53, 2.50, 6.70, 0.46, 28, "paper", bold=True)
text(s, "安排四个角色，传递任务与结果", 5.55, 3.06, 6.58, 0.30, 17, "paper")
text(s, "FastAPI + LangGraph", 10.13, 3.43, 2.06, 0.17, 9.5, "mint", align=PP_ALIGN.RIGHT)
line(s, 8.88, 3.67, 8.88, 4.18, "mint", 1.8, arrow=True, both=True)
line(s, 2.67, 4.18, 10.64, 4.18, "night_line", 1.8)
lower = [
    (0.78, "语言模型", "理解 · 选择 · 审阅 · 解释", "Qwen + vLLM", "mint"),
    (4.77, "计算与验算", "运行数学工具，产生检查证据", "SymPy / SciPy / NumPy", "mint"),
    (8.76, "任务记录", "保存问题、结果与事件历史", "SQLite", "mint"),
]
for x, title, desc, tech, accent in lower:
    center = x + 1.885
    line(s, center, 4.18, center, 4.57, "mint", 1.8, arrow=True, both=True)
    shape(s, x, 4.66, 3.77, 1.44, "night_card", "night_line", radius=True)
    text(s, title, x + 0.24, 4.88, 3.29, 0.40, 24, "paper", bold=True)
    text(s, desc, x + 0.24, 5.43, 3.28, 0.28, 13.7, "night_muted")
    text(s, tech, x + 0.24, 5.85, 3.22, 0.18, 10.5, accent)
text(s, "部署：前端文件、后端与模型在内网；公网入口只转发请求。", 0.82, 6.35, 11.73, 0.30, 14.5, "night_muted")
text(s, "理解  →  计算  →  检查  →  解释", 0.82, 6.76, 11.73, 0.24, 14.5, "mint", bold=True)
add_notes(s, 5)


# Validate geometry and notes before writing the artifact.
assert len(prs.slides) == 5
for page, slide in enumerate(prs.slides, 1):
    assert len(slide.notes_slide.notes_text_frame.text) > 100
    for obj in slide.shapes:
        # Suppress the stock Office theme shadows for a clean, flat diagram.
        props = obj._element.find(qn("p:spPr"))
        if props is not None:
            for effect in list(props.findall(qn("a:effectLst"))):
                props.remove(effect)
            props.append(OxmlElement("a:effectLst"))
        assert obj.left >= 0 and obj.top >= 0, (page, obj.name, "negative position")
        assert obj.left + obj.width <= prs.slide_width + 10, (page, obj.name, "outside right edge")
        assert obj.top + obj.height <= prs.slide_height + 10, (page, obj.name, "outside bottom edge")
prs.save(PPTX)
manifest = {"slides": 5, "outline": OUTLINE.name, "outline_sha256": hashlib.sha256(OUTLINE.read_bytes()).hexdigest(),
            "presentation": PPTX.name, "editable_text_and_shapes": True,
            "speaker_notes": True, "font": FONT, "example_root": 1.4142135623731364,
            "example_residual": 1.1723955140041653e-13}
(HERE / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Created {PPTX} ({len(prs.slides)} slides)")
