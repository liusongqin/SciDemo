from __future__ import annotations

from collections import Counter
from typing import Any


def requested_tool_counts(query: str) -> dict[str, int]:
    """Infer only explicit, mechanically checkable tool goals from the request."""
    counts: dict[str, int] = {}
    lowered = query.lower()
    if "所有实根" in query or "所有的实根" in query:
        counts["solve_symbolic_equation"] = 1
    if "导数" in query or "求导" in query:
        counts["differentiate_expression"] = 1
    if "驻点" in query:
        counts["solve_symbolic_equation"] = max(2 if "所有实根" in query else 1, counts.get("solve_symbolic_equation", 0))
    if "驻点处的值" in query or "驻点处函数值" in query:
        counts["evaluate_expression"] = 2 if "两个驻点" in query or "分别" in query else 1
    if any(word in lowered for word in ("绘制", "画出", "画图", "作图", "可视化", "函数图像", "函数图形")):
        counts["plot_function"] = 1
    return counts


def missing_tool_goals(query: str, history: list[dict[str, Any]]) -> list[str]:
    completed = Counter(
        item.get("tool") for item in history
        if item.get("verification", {}).get("passed")
    )
    # These tools produce a chart from their own structured result.  Requiring a
    # second plot_function call would ask the model to recreate data it cannot
    # express as a single analytic function (notably cubic splines).
    lowered = query.lower()
    auto_visual_tools: set[str] = set()
    if "插值" in lowered:
        auto_visual_tools.add("interpolate_data")
    if "拟合" in lowered:
        auto_visual_tools.add("fit_curve")
    if "ode" in lowered or "常微分" in lowered or "初值" in lowered:
        auto_visual_tools.add("solve_ode")
    if any(completed[tool] for tool in auto_visual_tools):
        completed["plot_function"] = max(completed["plot_function"], 1)
    labels = {
        "solve_symbolic_equation": "符号解方程（函数实根/驻点）",
        "differentiate_expression": "计算导数",
        "evaluate_expression": "计算驻点处函数值",
        "plot_function": "绘制用户指定区间的函数图像",
    }
    return [
        f"{labels.get(tool, tool)}：还需 {required - completed[tool]} 次"
        for tool, required in requested_tool_counts(query).items()
        if completed[tool] < required
    ]
