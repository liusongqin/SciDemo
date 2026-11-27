#!/usr/bin/env python3
"""Build the editable five-slide deck from the previously written Markdown outline."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt


HERE = Path(__file__).resolve().parent
OUT = HERE.parent
OUTLINE = OUT / "SciDemo_Agent原理架构与开发思路_逐页方案.md"
PPTX = OUT / "SciDemo_Agent原理架构与开发思路.pptx"
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
prs.core_properties.title = "SciDemo：Agent 原理、架构与开发思路"
prs.core_properties.subject = "面向理科学生的 Agent 原理、四角色协作、系统架构与开发演进"
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
    text(slide, note or "SCIDEMO  /  原理 · 架构 · 开发", 0.68, 7.12, 10.9, 0.19, 9.5, tc)
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
    match = re.search(rf"## 第 {number} 页[｜|].*?(?=\n## 第 \d 页|\n## 核验说明|\Z)", outline, re.S)
    assert match, number
    page = match.group(0)
    speech = re.search(r"### 建议讲解稿\n(.*?)(?=\n### |\Z)", page, re.S).group(1).strip()
    takeaway = re.search(r"### 学生带走的一句话\n(.*?)(?=\n### |\Z)", page, re.S).group(1).strip()
    content = f"第 {number} 页\n\n讲解稿：\n{speech}\n\n本页结论：\n{takeaway.replace('**', '')}"
    if number == 4:
        evidence = re.search(r"### 开发历史依据.*?\n(.*?)(?=\n### |\Z)", page, re.S).group(1).strip()
        content += "\n\n开发依据：\n" + evidence
    slide.notes_slide.notes_text_frame.text = content


# 1. Define the mechanism through a familiar scientific question.
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid()
s.background.fill.fore_color.rgb = rgb("night")
text(s, "SCIDEMO  /  AGENT 原理 · 架构 · 开发", 0.74, 0.45, 11.9, 0.28, 12, "mint", bold=True)
text(s, "让一句提问，变成\n可检查的求解过程", 0.72, 1.25, 7.15, 1.60, 39, "paper", bold=True, spacing=1.18)
text(s, "模型提出动作，程序执行计算，\n结果影响下一步。", 0.77, 3.20, 6.60, 0.84, 21, "night_muted", spacing=1.25)

cycle = [(8.00, 2.02, "读目标"), (10.51, 2.02, "选工具"),
         (10.51, 3.34, "看结果"), (8.00, 3.34, "再决定")]
for x, y, label in cycle:
    shape(s, x, y, 2.07, 0.78, "night_card", "night_line", radius=True)
    text(s, label, x, y + 0.18, 2.07, 0.40, 22, "paper", bold=True, align=PP_ALIGN.CENTER)
line(s, 10.15, 2.41, 10.43, 2.41, "mint", 2, arrow=True)
line(s, 11.55, 2.90, 11.55, 3.23, "mint", 2, arrow=True)
line(s, 10.43, 3.73, 10.15, 3.73, "mint", 2, arrow=True)
line(s, 9.03, 3.23, 9.03, 2.90, "mint", 2, arrow=True)
text(s, "反馈循环", 9.40, 2.92, 1.79, 0.28, 12, "mint", bold=True, align=PP_ALIGN.CENTER)

shape(s, 0.75, 4.65, 11.83, 0.93, "night_card", radius=True)
text(s, "例：求 x²−2=0 的正数解，并说明怎么检查。", 1.00, 4.90, 7.65, 0.40, 20, "paper", bold=True)
text(s, "x ≈ 1.41421356", 9.00, 4.90, 3.29, 0.40, 23, "mint", bold=True, align=PP_ALIGN.RIGHT)
parts = [(0.75, "模型决策"), (3.83, "工具执行"), (6.91, "状态记录"), (9.99, "反馈循环")]
for i, (x, label) in enumerate(parts):
    shape(s, x, 6.10, 2.56, 0.55, "night_card", radius=True)
    text(s, label, x, 6.22, 2.56, 0.31, 19, "paper", bold=True, align=PP_ALIGN.CENTER)
    if i < 3:
        text(s, "+", x + 2.63, 6.17, 0.37, 0.37, 22, "mint", align=PP_ALIGN.CENTER)
footer(s, 1, True)
add_notes(s, 1)

# 2. Explicit role handoffs: the reviewer returns to the solver, not straight to the reporter.
s = base(2, "四角色协作", "四种职责，共同推进一道题", "读题、求解、检查与报告，通过共享任务记录协作。")
xs = [0.70, 3.78, 6.86, 9.94]
roles = [
    ("问题分析员", "读懂条件与目标", "blue", "blue_bg"),
    ("科学求解员", "选择工具，\n根据结果决定下一步", "teal", "teal_bg"),
    ("验证审查员", "程序验算后，\n由模型复核当前步骤", "orange", "orange_bg"),
    ("报告撰写员", "根据已验证记录，\n组织结果与说明", "blue", "blue_bg"),
]
for i, (title, desc, accent, tint) in enumerate(roles):
    x = xs[i]
    shape(s, x, 3.00, 2.70, 2.31, "paper", "line", radius=True)
    circle(s, x + 0.21, 3.23, 0.40, accent, str(i + 1), 14.5)
    text(s, title, x + 0.21, 3.88, 2.28, 0.42, 22, "ink", bold=True)
    text(s, desc, x + 0.21, 4.56, 2.28, 0.57, 15, "muted", spacing=1.22)
line(s, 3.48, 4.08, 3.69, 4.08, "muted", 1.8, arrow=True)
line(s, 6.56, 4.08, 6.77, 4.08, "muted", 1.8, arrow=True)
line(s, 5.13, 2.94, 5.13, 2.35, "blue", 1.8)
line(s, 5.13, 2.35, 11.29, 2.35, "blue", 1.8)
line(s, 11.29, 2.35, 11.29, 2.94, "blue", 1.8, arrow=True)
shape(s, 7.05, 2.11, 2.45, 0.44, "bg")
text(s, "可汇总时，交报告员", 7.08, 2.20, 2.40, 0.30, 14.5, "blue", bold=True, align=PP_ALIGN.CENTER)
line(s, 8.21, 5.34, 8.21, 5.79, "orange", 1.8)
line(s, 8.21, 5.79, 5.13, 5.79, "orange", 1.8)
line(s, 5.13, 5.79, 5.13, 5.35, "orange", 1.8, arrow=True)
shape(s, 5.37, 5.56, 2.61, 0.45, "bg")
text(s, "带着证据继续判断", 5.41, 5.66, 2.53, 0.29, 14.3, "orange", bold=True, align=PP_ALIGN.CENTER)
shape(s, 0.73, 6.25, 11.9, 0.47, "teal_bg", radius=True)
text(s, "当前步骤通过 = 程序检查通过，且模型复核同意", 0.99, 6.35, 11.34, 0.30, 18, "teal", bold=True, align=PP_ALIGN.CENTER)
text(s, "四角色可共用一个模型；角色骨架由代码规定，具体工具由模型选择。", 0.77, 6.84, 11.80, 0.18, 10, "muted")
add_notes(s, 2)

# 3. Architecture: presentation, orchestration, model, tools, and persistent records.
s = base(3, "系统架构", "流程控制中心，把各个部件串起来", "模型负责选择，程序执行计算，任务记录连接前后步骤。", True)
shape(s, 0.78, 2.29, 3.33, 1.36, "night_card", "night_line", radius=True)
text(s, "网页界面", 1.02, 2.51, 2.85, 0.42, 25, "paper", bold=True)
text(s, "提问 · 看结果 · 参与审核", 1.03, 3.02, 2.88, 0.30, 14.1, "night_muted")
text(s, "Vue", 1.03, 3.39, 2.70, 0.18, 10, "mint")
line(s, 4.25, 2.98, 5.07, 2.98, "mint", 2.0, arrow=True, both=True)
text(s, "问题", 4.18, 2.57, 0.96, 0.23, 11.5, "night_muted", align=PP_ALIGN.CENTER)
text(s, "进度与结果", 4.18, 3.16, 0.96, 0.23, 9.6, "night_muted", align=PP_ALIGN.CENTER)
shape(s, 5.23, 2.29, 7.30, 1.36, "teal", radius=True)
text(s, "流程控制中心", 5.53, 2.50, 6.70, 0.46, 28, "paper", bold=True)
text(s, "安排角色、校验动作、传递结果", 5.55, 3.06, 6.58, 0.30, 17, "paper")
text(s, "FastAPI + LangGraph", 10.13, 3.43, 2.06, 0.17, 9.5, "mint", align=PP_ALIGN.RIGHT)
line(s, 8.88, 3.67, 8.88, 4.18, "mint", 1.8, arrow=True, both=True)
line(s, 2.67, 4.18, 10.64, 4.18, "night_line", 1.8)
lower = [
    (0.78, "语言模型", "理解 · 选择 · 审阅 · 解释", "Qwen + vLLM"),
    (4.77, "计算与验算", "运行数学工具，产生检查证据", "SymPy / SciPy / NumPy"),
    (8.76, "任务记录", "保存问题、结果与事件历史", "SQLite"),
]
for x, title, desc, tech in lower:
    line(s, x + 1.885, 4.18, x + 1.885, 4.57, "mint", 1.8, arrow=True, both=True)
    shape(s, x, 4.66, 3.77, 1.44, "night_card", "night_line", radius=True)
    text(s, title, x + 0.24, 4.88, 3.29, 0.40, 24, "paper", bold=True)
    text(s, desc, x + 0.24, 5.43, 3.28, 0.28, 13.7, "night_muted")
    text(s, tech, x + 0.24, 5.85, 3.22, 0.18, 10.5, "mint")
text(s, "部署：前端文件、后端与模型在内网；公网入口只转发请求。", 0.82, 6.38, 11.73, 0.30, 14.5, "night_muted")
text(s, "网页呈现  ·  流程协调  ·  模型与计算程序各司其职", 0.82, 6.79, 11.73, 0.22, 13.5, "mint", bold=True)
add_notes(s, 3)

# 4. The real development sequence, keeping early foundations distinct from later additions.
s = base(4, "开发演进", "每增加一点自主性，就补检查与记录", "从实际开发经历出发：先完成计算闭环，再逐步扩大能力。")
stages = [
    ("最小计算闭环", "先跑通工具计算、验算，\n再生成解释", "blue"),
    ("自主反馈循环", "模型根据结果，\n选择继续调用或结束", "teal"),
    ("复杂任务处理", "扩工具、反馈参数错误，\n减少重复调用", "teal"),
    ("四角色协作", "拆分职责、增加模型复核，\n记录每次角色交接", "orange"),
    ("对话与会话体验", "多轮上下文、身份与历史，\n支持不同模型后端", "blue"),
    ("本次部署完善", "独立管理服务与隧道，\n内网运行，公网纯转发", "teal"),
]
stage_x = [0.72, 4.82, 8.92]
for i, (title, desc, accent) in enumerate(stages):
    x = stage_x[i % 3]
    y = 2.30 if i < 3 else 4.44
    shape(s, x, y, 3.69, 1.75, "paper", "line", radius=True)
    circle(s, x + 0.20, y + 0.24, 0.40, accent, str(i + 1), 14.5)
    text(s, title, x + 0.79, y + 0.25, 2.68, 0.37, 18.7, "ink", bold=True)
    text(s, desc, x + 0.22, y + 0.91, 3.23, 0.62, 14.8, "muted", spacing=1.24)
    if i % 3 < 2:
        line(s, x + 3.76, y + 0.87, stage_x[i % 3 + 1] - 0.09, y + 0.87, "muted", 1.7, arrow=True)
line(s, 10.76, 4.07, 10.76, 4.25, "line", 1.4)
line(s, 10.76, 4.25, 2.57, 4.25, "line", 1.4)
line(s, 2.57, 4.25, 2.57, 4.39, "muted", 1.4, arrow=True)
shape(s, 0.74, 6.43, 11.85, 0.41, "teal_bg", radius=True)
text(s, "开发思路：逐步增加能力，同时让每一步更可检查、更容易维护。", 0.97, 6.51, 11.38, 0.29, 16.5, "teal", bold=True, align=PP_ALIGN.CENTER)
add_notes(s, 4)

# 5. Separate implemented safeguards from future work.
s = base(5, "设计取舍与下一步", "自主性，需要证据与边界", "先明确现有设计，再把下一步要完善的内容说清楚。", True)
principles = [("允许做什么？", "工具白名单与参数校验"),
              ("结果凭什么？", "程序检查与模型复核"),
              ("过程怎么看？", "共享状态与执行事件")]
for i, (question, answer) in enumerate(principles):
    x = stage_x[i]
    shape(s, x, 2.30, 3.69, 1.23, "night_card", "night_line", radius=True)
    text(s, question, x + 0.24, 2.52, 3.21, 0.36, 21, "paper", bold=True)
    text(s, answer, x + 0.24, 3.07, 3.21, 0.29, 15.2, "mint")
text(s, "下一步，优先完善三件事", 0.78, 3.92, 11.78, 0.36, 21, "paper", bold=True)
for label, x, width in [("方向", 0.82, 2.12), ("当前边界", 3.18, 4.23), ("改进方向", 7.88, 4.46)]:
    text(s, label, x, 4.49, width, 0.25, 12.8, "night_muted", bold=True)
line(s, 0.78, 4.82, 12.55, 4.82, "night_line", 1.0)
improvements = [
    ("完成条件", "目标提示不硬性保证全部完成", "把需求变成可核对的验收项"),
    ("验证覆盖", "部分工具仅做基础检查", "加强交叉验证与反例测试"),
    ("任务恢复", "运行任务仍有内存状态", "持久化检查点与恢复流程"),
]
for i, (area, limitation, future) in enumerate(improvements):
    y = 4.98 + i * 0.54
    text(s, area, 0.82, y, 2.12, 0.32, 17.0, "paper", bold=True)
    text(s, limitation, 3.18, y + 0.025, 4.25, 0.28, 14.2, "night_muted")
    text(s, future, 7.88, y + 0.025, 4.45, 0.28, 14.2, "mint")
    line(s, 0.78, y + 0.40, 12.55, y + 0.40, "night_line", 0.6)
text(s, "把自主决策与可检查的程序边界一起设计。", 0.81, 6.67, 11.75, 0.29, 16, "mint", bold=True)
add_notes(s, 5)


assert len(prs.slides) == 5
for page, slide in enumerate(prs.slides, 1):
    assert len(slide.notes_slide.notes_text_frame.text) > 100
    for obj in slide.shapes:
        props = obj._element.find(qn("p:spPr"))
        if props is not None:
            for effect in list(props.findall(qn("a:effectLst"))):
                props.remove(effect)
            props.append(OxmlElement("a:effectLst"))
        style = obj._element.find(qn("p:style"))
        if style is not None:
            effect_ref = style.find(qn("a:effectRef"))
            if effect_ref is not None:
                effect_ref.set("idx", "0")
        assert obj.left >= 0 and obj.top >= 0, (page, obj.name)
        assert obj.left + obj.width <= prs.slide_width + 10, (page, obj.name)
        assert obj.top + obj.height <= prs.slide_height + 10, (page, obj.name)
prs.save(PPTX)
manifest = {"slides": 5, "outline": OUTLINE.name, "outline_sha256": hashlib.sha256(OUTLINE.read_bytes()).hexdigest(),
            "presentation": PPTX.name, "editable": True, "speaker_notes": True, "font": FONT,
            "topics": ["项目原理", "四角色协作", "系统架构", "开发演进", "设计取舍与改进"]}
(HERE / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Created {PPTX} ({len(prs.slides)} slides)")


