import pytest
from app.science import TOOLS, safe_expression, find_root, differentiate_expression, interpolate_data, fit_curve, plot_artifact, solve_symbolic_equation

def test_safe_expression_rejects_code():
    with pytest.raises(ValueError): safe_expression("__import__('os').system('id')")

def test_newton_root_is_verified():
    result=find_root(expression="cos(x)-x",method="newton",initial=.5,tolerance=1e-10)
    assert result["converged"] and result["residual"]<1e-10

def test_single_root_record_does_not_create_misleading_chart():
    result={"method":"brentq","iterations":[{"iteration":9,"x":1.5,"residual":1e-12}]}
    assert plot_artifact("root",result)=={}

def test_multi_step_root_trace_has_method_aware_title():
    result={"method":"newton","iterations":[{"iteration":0,"residual":1.0},{"iteration":1,"residual":1e-3}]}
    artifact=plot_artifact("root",result)
    assert artifact["title"]=="Newton 迭代收敛过程"
    assert artifact["layout"]["xaxis"]["dtick"]==1

def test_symbolic_derivative():
    assert differentiate_expression("x^2")["expression"]=="2*x"

def test_cubic_symbolic_roots_include_verifiable_approximations():
    result=solve_symbolic_equation("x^3-3*x+1")
    assert len(result["solutions"])==3 and len(result["approximations"])==3
    assert max(abs(v["real"]**3-3*v["real"]+1) for v in result["approximations"])<1e-12

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

def test_expression_transform_limit_and_multiple_integral():
    assert TOOLS["transform_expression"]("(x+y)^2","expand")["expression"]=="x**2 + 2*x*y + y**2"
    assert TOOLS["calculate_limit"]("sin(x)/x","x",0,"both")["expression"]=="1"
    assert TOOLS["integrate_multiple"]("x+y",["x","y"],[0,0],[1,1])["expression"]=="1"

def test_multivariate_derivative_and_symbolic_system():
    derivative=TOOLS["multivariate_derivative"]("x^2+x*y+y^2",["x","y"],"hessian")
    assert derivative["shape"]==[2,2]
    system=TOOLS["solve_symbolic_system"](["x+y-2","x-y"],["x","y"])
    assert system["solutions"]==[{"x":"1","y":"1"}]

def test_matrix_operations_and_symbolic_ode():
    assert TOOLS["matrix_calculation"]("determinant",[[1,2],[3,4]])["result"]=="-2"
    solved=TOOLS["matrix_calculation"]("solve_linear",[[2,0],[0,4]],vector=[6,8])
    assert solved["result"]==[["3"],["2"]]
    ode=TOOLS["solve_symbolic_ode"]("y",y0=2,t0=0)
    assert "2*exp(t)" in ode["solution_expression"]

def test_implicit_and_surface_plots():
    implicit=TOOLS["plot_implicit"]("x^2+y^2-1",[-2,2],[-2,2],30)
    surface=TOOLS["plot_surface"]("x^2+y^2",[-1,1],[-1,1],20)
    assert implicit["data"][0]["type"]=="contour" and len(implicit["data"][0]["z"])==30
    assert surface["data"][0]["type"]=="surface" and len(surface["data"][0]["z"])==20
