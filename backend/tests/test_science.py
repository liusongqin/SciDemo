import pytest
from app.science import TOOLS, safe_expression, find_root, differentiate_expression, interpolate_data, fit_curve

def test_safe_expression_rejects_code():
    with pytest.raises(ValueError): safe_expression("__import__('os').system('id')")

def test_newton_root_is_verified():
    result=find_root(expression="cos(x)-x",method="newton",initial=.5,tolerance=1e-10)
    assert result["converged"] and result["residual"]<1e-10

def test_symbolic_derivative():
    assert differentiate_expression("x^2")["expression"]=="2*x"

def test_interpolation_nodes():
    assert interpolate_data([0,1,2],[0,1,0])["node_error"]<1e-12

def test_fit_metrics():
    result=fit_curve([0,1,2,3],[1,4,9,16],degree=2)
    assert result["rmse"]<1e-10 and result["r2"]>.999

def test_plot_function_is_registered_and_executable():
    result=TOOLS["plot_function"](expression="x^2",start=-2,end=2,variable="x",samples=25)
    assert result["kind"]=="plotly" and len(result["data"][0]["x"])==25

def test_plot_function_rejects_unbound_variables():
    with pytest.raises(ValueError,match="未赋值变量"):
        TOOLS["plot_function"](expression="y",start=0,end=4,variable="x",samples=25)
