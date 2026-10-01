from __future__ import annotations
import ast, math
from typing import Any, Literal
import numpy as np
import sympy as sp
from pydantic import BaseModel, Field, field_validator
from scipy import integrate, interpolate, optimize

MAX_POINTS = 2000
ALLOWED_NAMES = {name: sp.Symbol(name, real=True) for name in ("x", "y", "z", "t")}
ALLOWED_CONST = {"pi": sp.pi, "E": sp.E}
ALLOWED_FUNCS = {"sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "exp": sp.exp, "log": sp.log,
                 "sqrt": sp.sqrt, "abs": sp.Abs, "sinh": sp.sinh, "cosh": sp.cosh}
ALLOWED_BIN = {ast.Add: lambda a,b:a+b, ast.Sub: lambda a,b:a-b, ast.Mult: lambda a,b:a*b,
               ast.Div: lambda a,b:a/b, ast.Pow: lambda a,b:a**b}
ALLOWED_UNARY = {ast.UAdd: lambda a:a, ast.USub: lambda a:-a}

def safe_expression(text: str) -> sp.Expr:
    if len(text) > 500: raise ValueError("表达式过长（最多 500 字符）")
    text = text.replace("^", "**")
    try: tree = ast.parse(text, mode="eval")
    except SyntaxError as exc: raise ValueError("表达式语法无效") from exc
    if sum(1 for _ in ast.walk(tree)) > 100: raise ValueError("表达式过于复杂")
    def build(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return sp.Float(node.value) if isinstance(node.value,float) else sp.Integer(node.value)
        if isinstance(node, ast.Name):
            if node.id in ALLOWED_NAMES: return ALLOWED_NAMES[node.id]
            if node.id in ALLOWED_CONST: return ALLOWED_CONST[node.id]
            raise ValueError(f"不允许的名称: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_BIN: return ALLOWED_BIN[type(node.op)](build(node.left), build(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_UNARY: return ALLOWED_UNARY[type(node.op)](build(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ALLOWED_FUNCS and len(node.args)==1:
            return ALLOWED_FUNCS[node.func.id](build(node.args[0]))
        raise ValueError("表达式含有不允许的语法")
    return build(tree.body)

class ExpressionInput(BaseModel):
    expression: str = Field(min_length=1, max_length=500)
    variable: Literal["x", "y", "z", "t"] = "x"
    @field_validator("expression")
    @classmethod
    def validate_expr(cls, v): safe_expression(v); return v

class RootInput(ExpressionInput):
    method: Literal["newton", "bisect", "brentq"] = "newton"
    initial: float = 0.5
    bracket: tuple[float, float] = (0.0, 1.0)
    tolerance: float = Field(default=1e-8, gt=0, le=0.1)
    max_iterations: int = Field(default=50, ge=1, le=200)

class DataInput(BaseModel):
    x: list[float] = Field(min_length=2, max_length=500)
    y: list[float] = Field(min_length=2, max_length=500)
    @field_validator("y")
    @classmethod
    def finite(cls, v):
        if not all(math.isfinite(a) for a in v): raise ValueError("数据必须有限")
        return v

def _json(value: Any):
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    return value

def simplify_expression(expression: str):
    value = sp.simplify(safe_expression(expression)); return {"expression": str(value), "latex": sp.latex(value)}

def transform_expression(expression: str, operation="expand"):
    source=safe_expression(expression)
    operations={"expand":sp.expand,"factor":sp.factor,"cancel":sp.cancel,"trigsimp":sp.trigsimp}
    if operation not in operations: raise ValueError("表达式变换仅支持 expand/factor/cancel/trigsimp")
    value=operations[operation](source)
    return {"source":str(source),"expression":str(value),"latex":sp.latex(value),"operation":operation}

def calculate_limit(expression: str, variable="x", point=0.0, direction="both"):
    source=safe_expression(expression); x=ALLOWED_NAMES[variable]
    if direction not in ("both","left","right"): raise ValueError("极限方向仅支持 both/left/right")
    direction_arg={"both":"+-","left":"-","right":"+"}[direction]
    value=sp.limit(source,x,point,dir=direction_arg)
    return {"expression":str(value),"latex":sp.latex(value),"source":str(source),"variable":variable,"point":point,"direction":direction}

def differentiate_expression(expression: str, variable="x", order=1):
    if order not in range(1, 6): raise ValueError("求导阶数必须为 1 到 5")
    source=safe_expression(expression); result=sp.diff(source, ALLOWED_NAMES[variable], order)
    return {"expression": str(result), "latex": sp.latex(result), "source": str(source), "variable": variable, "order": order}

def integrate_expression(expression: str, variable="x", lower=None, upper=None):
    source=safe_expression(expression); x=ALLOWED_NAMES[variable]
    result=sp.integrate(source, x) if lower is None or upper is None else sp.integrate(source,(x,lower,upper))
    return {"expression": str(result), "latex": sp.latex(result), "definite": lower is not None and upper is not None}

def integrate_multiple(expression: str, variables: list[str], lower_bounds: list[float], upper_bounds: list[float]):
    if not 2 <= len(variables) <= 3 or len(set(variables))!=len(variables): raise ValueError("多重积分支持 2 到 3 个不同变量")
    if len(lower_bounds)!=len(variables) or len(upper_bounds)!=len(variables): raise ValueError("变量与积分上下限数量必须一致")
    limits=[]
    for name,lower,upper in zip(variables,lower_bounds,upper_bounds):
        if name not in ALLOWED_NAMES or lower>=upper: raise ValueError("积分变量无效或上下限未递增")
        limits.append((ALLOWED_NAMES[name],lower,upper))
    result=sp.integrate(safe_expression(expression),*limits)
    return {"expression":str(result),"latex":sp.latex(result),"variables":variables,"limits":[[a,b] for a,b in zip(lower_bounds,upper_bounds)]}

def multivariate_derivative(expression: str, variables: list[str], operation="gradient"):
    source=safe_expression(expression)
    if not 1 <= len(variables) <= 4 or any(v not in ALLOWED_NAMES for v in variables): raise ValueError("微分变量必须是 x/y/z/t")
    syms=[ALLOWED_NAMES[v] for v in variables]
    if operation=="gradient": value=sp.Matrix([sp.diff(source,s) for s in syms])
    elif operation=="hessian": value=sp.hessian(source,syms)
    else: raise ValueError("多变量微分仅支持 gradient/hessian")
    return {"operation":operation,"variables":variables,"expression":str(value),"latex":sp.latex(value),"shape":list(value.shape)}

def solve_symbolic_equation(expression: str, variable="x"):
    expr=safe_expression(expression); sols=sp.solve(expr, ALLOWED_NAMES[variable])
    approximations=[]
    for solution in sols:
        value=complex(sp.N(solution,30))
        approximations.append({"real":float(value.real),"imag":float(value.imag)})
    return {"solutions": [str(s) for s in sols], "latex": [sp.latex(s) for s in sols],
            "approximations":approximations,"expression": str(expr)}

def solve_symbolic_system(expressions: list[str], variables: list[str]):
    if not 1 <= len(expressions) <= 4 or len(variables)>4: raise ValueError("方程组限 1 到 4 个方程/变量")
    exprs=[safe_expression(e) for e in expressions]; syms=[ALLOWED_NAMES[v] for v in variables]
    return {"solutions": [{str(k):str(v) for k,v in s.items()} for s in sp.solve(exprs,syms,dict=True)]}

def matrix_calculation(operation: str, matrix_a: list[list[float]], matrix_b: list[list[float]]|None=None, vector: list[float]|None=None):
    a=sp.Matrix(matrix_a)
    if not 1 <= a.rows <= 8 or not 1 <= a.cols <= 8: raise ValueError("矩阵维度限制为 1 到 8")
    b=sp.Matrix(matrix_b) if matrix_b is not None else None
    if operation=="transpose": value=a.T
    elif operation=="determinant": value=a.det()
    elif operation=="rank": value=a.rank()
    elif operation=="inverse":
        if a.rows!=a.cols or a.det()==0: raise ValueError("矩阵不可逆")
        value=a.inv()
    elif operation in ("add","subtract","multiply"):
        if b is None: raise ValueError("该运算需要 matrix_b")
        value={"add":lambda:a+b,"subtract":lambda:a-b,"multiply":lambda:a*b}[operation]()
    elif operation=="eigenvalues": value={str(k):int(v) for k,v in a.eigenvals().items()}
    elif operation=="eigenvectors": value=[{"value":str(item[0]),"multiplicity":item[1],"vectors":[[str(v) for v in vec] for vec in item[2]]} for item in a.eigenvects()]
    elif operation=="solve_linear":
        if vector is None or len(vector)!=a.rows: raise ValueError("线性方程组需要与矩阵行数一致的 vector")
        value=a.solve_least_squares(sp.Matrix(vector)) if a.rows!=a.cols else a.inv()*sp.Matrix(vector)
    else: raise ValueError("未知矩阵运算")
    if isinstance(value,sp.MatrixBase): serialized=[[str(value[i,j]) for j in range(value.cols)] for i in range(value.rows)]
    else: serialized=value if isinstance(value,(dict,list,int)) else str(value)
    return {"operation":operation,"result":serialized,"shape":list(a.shape)}

def solve_symbolic_ode(rhs: str, y0: float|None=None, t0: float=0.0):
    t=ALLOWED_NAMES["t"]; y=sp.Function("y"); source=safe_expression(rhs).subs(ALLOWED_NAMES["y"],y(t))
    equation=sp.Eq(sp.diff(y(t),t),source); ics={y(t0):y0} if y0 is not None else None
    solution=sp.dsolve(equation,ics=ics)
    return {"equation":str(equation),"solution":str(solution),"solution_expression":str(solution.rhs),"rhs":rhs,
            "latex":sp.latex(solution),"initial_condition":None if y0 is None else {"t0":t0,"y0":y0}}

def series_expansion(expression: str, variable="x", point=0.0, order=6):
    if not 1 <= order <= 12: raise ValueError("展开阶数限 1 到 12")
    result=sp.series(safe_expression(expression),ALLOWED_NAMES[variable],point,order)
    return {"expression":str(result),"latex":sp.latex(result)}

def evaluate_expression(expression: str, values: dict[str,float]):
    expr=safe_expression(expression); result=float(expr.evalf(subs={ALLOWED_NAMES[k]:v for k,v in values.items()}))
    if not math.isfinite(result): raise ValueError("结果为 NaN 或无穷大")
    return {"value":result,"values":values}

def find_root(**kwargs):
    p=RootInput(**kwargs); expr=safe_expression(p.expression); x=ALLOWED_NAMES[p.variable]
    f=sp.lambdify(x,expr,"numpy"); df=sp.lambdify(x,sp.diff(expr,x),"numpy"); iterations=[]
    if p.method == "newton":
        current=p.initial
        for i in range(p.max_iterations):
            fv=float(f(current)); dv=float(df(current)); iterations.append({"iteration":i,"x":current,"residual":abs(fv)})
            if abs(fv)<=p.tolerance: break
            if abs(dv)<1e-14: raise ValueError("Newton 法遇到近零导数")
            current -= fv/dv
        root=current; converged=abs(float(f(root)))<=p.tolerance
    else:
        a,b=p.bracket
        if float(f(a))*float(f(b))>0: raise ValueError("区间端点函数值同号，无法保证存在根")
        fn=optimize.bisect if p.method=="bisect" else optimize.brentq
        result=fn(f,a,b,xtol=p.tolerance,maxiter=p.max_iterations,full_output=True); root=float(result[0]); converged=result[1].converged
        iterations=[{"iteration":result[1].iterations,"x":root,"residual":abs(float(f(root)))}]
    return {"root":float(root),"iterations":iterations,"iteration_count":len(iterations),"residual":abs(float(f(root))),"converged":bool(converged),"method":p.method,"expression":str(expr)}

def interpolate_data(x: list[float], y: list[float], method="cubic", samples=200):
    DataInput(x=x,y=y)
    if len(x)!=len(y) or len(set(x))!=len(x): raise ValueError("x/y 长度须一致且 x 不可重复")
    if method not in ("linear","cubic"): raise ValueError("插值方法仅支持 linear/cubic")
    if method=="cubic" and len(x)<3: raise ValueError("三次样条至少需要 3 个点")
    xs=np.linspace(min(x),max(x),min(samples,MAX_POINTS)); fn=interpolate.CubicSpline(x,y) if method=="cubic" else interpolate.interp1d(x,y)
    ys=fn(xs); node_error=float(np.max(np.abs(fn(np.array(x))-np.array(y))))
    return {"x":xs.tolist(),"y":ys.tolist(),"source_x":x,"source_y":y,"method":method,"node_error":node_error}

def fit_curve(x: list[float], y: list[float], degree=2):
    DataInput(x=x,y=y)
    if len(x)!=len(y) or degree not in range(1,6) or len(x)<=degree: raise ValueError("数据长度或多项式次数无效")
    coef=np.polyfit(x,y,degree); predicted=np.polyval(coef,x); residual=np.array(y)-predicted
    ss_res=float(np.sum(residual**2)); ss_tot=float(np.sum((np.array(y)-np.mean(y))**2))
    xs=np.linspace(min(x),max(x),200)
    return {"parameters":coef.tolist(),"predicted":predicted.tolist(),"residuals":residual.tolist(),"rmse":float(np.sqrt(np.mean(residual**2))),"max_residual":float(np.max(np.abs(residual))),"r2":1-ss_res/ss_tot if ss_tot else 1.0,"plot_x":xs.tolist(),"plot_y":np.polyval(coef,xs).tolist()}

def numerical_integral(expression: str, lower: float, upper: float, variable="x"):
    expr=safe_expression(expression); fn=sp.lambdify(ALLOWED_NAMES[variable],expr,"numpy")
    value,error=integrate.quad(fn,lower,upper,limit=100); trap=float(np.trapezoid(fn(np.linspace(lower,upper,1001)),np.linspace(lower,upper,1001)))
    return {"value":value,"estimated_error":error,"cross_check":trap,"difference":abs(value-trap)}

def solve_ode(rhs: str, y0: float, t_span: tuple[float,float], samples=200):
    expr=safe_expression(rhs); fn=sp.lambdify((ALLOWED_NAMES["t"],ALLOWED_NAMES["y"]),expr,"numpy"); ts=np.linspace(*t_span,min(samples,MAX_POINTS))
    sol=integrate.solve_ivp(lambda t,y:[fn(t,y[0])],t_span,[y0],t_eval=ts,rtol=1e-8,atol=1e-10)
    if not sol.success: raise ValueError(sol.message)
    slopes=np.gradient(sol.y[0],sol.t); residual=slopes-np.array([fn(t,y) for t,y in zip(sol.t,sol.y[0])])
    return {"t":sol.t.tolist(),"y":sol.y[0].tolist(),"success":True,"initial_error":abs(sol.y[0][0]-y0),"max_discrete_residual":float(np.max(np.abs(residual[1:-1])))}

TOOLS={"simplify_expression":simplify_expression,"transform_expression":transform_expression,"calculate_limit":calculate_limit,
       "differentiate_expression":differentiate_expression,"multivariate_derivative":multivariate_derivative,
       "integrate_expression":integrate_expression,"integrate_multiple":integrate_multiple,"matrix_calculation":matrix_calculation,
       "solve_symbolic_equation":solve_symbolic_equation,"solve_symbolic_system":solve_symbolic_system,"series_expansion":series_expansion,
       "solve_symbolic_ode":solve_symbolic_ode,"evaluate_expression":evaluate_expression,"find_root":find_root,"interpolate_data":interpolate_data,"fit_curve":fit_curve,
       "numerical_integral":numerical_integral,"solve_ode":solve_ode}

def plot_artifact(kind: str, result: dict[str,Any]):
    if kind=="root":
        it=result["iterations"]
        if len(it)<2: return {}
        method={"newton":"Newton","bisect":"二分法","brentq":"Brent"}.get(result.get("method"),"求根")
        return {"kind":"plotly","title":f"{method} 迭代收敛过程","data":[{"type":"scatter","mode":"lines+markers","name":"|f(x)|","x":[a["iteration"] for a in it],"y":[a["residual"] for a in it]}],"layout":{"yaxis":{"type":"log","title":"残差 |f(x)|"},"xaxis":{"title":"迭代次数","dtick":1}}}
    if kind=="interpolation":
        return {"kind":"plotly","title":"插值曲线","data":[{"type":"scatter","mode":"lines","name":"插值","x":result["x"],"y":result["y"]},{"type":"scatter","mode":"markers","name":"节点","x":result["source_x"],"y":result["source_y"]}],"layout":{}}
    if kind=="fit":
        return {"kind":"plotly","title":"曲线拟合","data":[{"type":"scatter","mode":"lines","name":"拟合曲线","x":result["plot_x"],"y":result["plot_y"]}],"layout":{}}
    if kind=="ode": return {"kind":"plotly","title":"ODE 数值解","data":[{"type":"scatter","mode":"lines","x":result["t"],"y":result["y"],"name":"y(t)"}],"layout":{}}
    return {}

def plot_function(expression: str, start=-5.0, end=5.0, variable="x", samples=400):
    if not start < end: raise ValueError("绘图区间必须递增")
    expr=safe_expression(expression); unexpected=expr.free_symbols-{ALLOWED_NAMES[variable]}
    if unexpected: raise ValueError(f"绘图表达式包含未赋值变量: {', '.join(sorted(str(item) for item in unexpected))}")
    xs=np.linspace(start,end,min(max(samples,2),MAX_POINTS)); fn=sp.lambdify(ALLOWED_NAMES[variable],expr,"numpy")
    ys=np.asarray(fn(xs),dtype=float); ys=np.broadcast_to(ys,xs.shape)
    finite=np.isfinite(ys)
    return {"kind":"plotly","title":"函数曲线","data":[{"type":"scatter","mode":"lines","x":xs[finite].tolist(),"y":ys[finite].tolist(),"name":expression}],"layout":{}}

def plot_implicit(expression: str, x_range: list[float], y_range: list[float], samples=100):
    if len(x_range)!=2 or len(y_range)!=2 or x_range[0]>=x_range[1] or y_range[0]>=y_range[1]: raise ValueError("绘图区间必须递增")
    expr=safe_expression(expression); unexpected=expr.free_symbols-{ALLOWED_NAMES["x"],ALLOWED_NAMES["y"]}
    if unexpected: raise ValueError("隐函数只能包含 x 和 y")
    count=min(max(samples,20),200); xs=np.linspace(*x_range,count); ys=np.linspace(*y_range,count); xx,yy=np.meshgrid(xs,ys)
    zz=np.asarray(sp.lambdify((ALLOWED_NAMES["x"],ALLOWED_NAMES["y"]),expr,"numpy")(xx,yy),dtype=float)
    return {"kind":"plotly","title":"隐函数曲线","data":[{"type":"contour","x":xs.tolist(),"y":ys.tolist(),"z":zz.tolist(),"contours":{"start":0,"end":0,"size":1},"showscale":False}],"layout":{}}

def plot_surface(expression: str, x_range: list[float], y_range: list[float], samples=60):
    if len(x_range)!=2 or len(y_range)!=2 or x_range[0]>=x_range[1] or y_range[0]>=y_range[1]: raise ValueError("绘图区间必须递增")
    expr=safe_expression(expression); unexpected=expr.free_symbols-{ALLOWED_NAMES["x"],ALLOWED_NAMES["y"]}
    if unexpected: raise ValueError("三维曲面表达式只能包含 x 和 y")
    count=min(max(samples,10),100); xs=np.linspace(*x_range,count); ys=np.linspace(*y_range,count); xx,yy=np.meshgrid(xs,ys)
    zz=np.asarray(sp.lambdify((ALLOWED_NAMES["x"],ALLOWED_NAMES["y"]),expr,"numpy")(xx,yy),dtype=float); zz=np.broadcast_to(zz,xx.shape)
    return {"kind":"plotly","title":"三维函数曲面","data":[{"type":"surface","x":xs.tolist(),"y":ys.tolist(),"z":zz.tolist()}],"layout":{}}

TOOLS["plot_function"] = plot_function
TOOLS["plot_implicit"] = plot_implicit
TOOLS["plot_surface"] = plot_surface

def plot_root_iterations(result: dict[str,Any]): return plot_artifact("root",result)
def plot_interpolation(result: dict[str,Any]): return plot_artifact("interpolation",result)
def plot_curve_fit(result: dict[str,Any]): return plot_artifact("fit",result)
def plot_ode_solution(result: dict[str,Any]): return plot_artifact("ode",result)
