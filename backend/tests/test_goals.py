from app.goals import missing_tool_goals, requested_tool_counts


QUERY = "求出所有实根，计算导数和两个驻点处的值，并绘制函数图像"


def item(tool, passed=True):
    return {"tool":tool,"verification":{"passed":passed}}


def test_explicit_multistep_goals_are_tracked():
    assert requested_tool_counts(QUERY)=={
        "solve_symbolic_equation":2,
        "differentiate_expression":1,
        "evaluate_expression":2,
        "plot_function":1,
    }
    history=[item("solve_symbolic_equation"),item("differentiate_expression"),
             item("solve_symbolic_equation"),item("evaluate_expression"),
             item("evaluate_expression"),item("plot_function")]
    assert missing_tool_goals(QUERY,history)==[]


def test_failed_calls_do_not_satisfy_goals():
    missing=missing_tool_goals(QUERY,[item("plot_function",False)])
    assert any("绘制" in goal for goal in missing)
