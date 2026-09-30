from __future__ import annotations
import asyncio, math, re, time
from typing import Any
import numpy as np
import sympy as sp
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from .models import ScientificAgentState
from .science import TOOLS, ALLOWED_NAMES, safe_expression, plot_artifact
from .storage import store
from .llm import MockModel, get_model
from .config import settings
from .registry import validate_call

KIND_TOOL={"root":"find_root","equation":"solve_symbolic_equation","derivative":"differentiate_expression",
 "integral":"integrate_expression","interpolation":"interpolate_data","fit":"fit_curve","ode":"solve_ode"}

def normalize_kind(kind: str, mode: str, fallback_kind: str) -> str:
    if kind not in KIND_TOOL: kind=fallback_kind
    if kind=="equation" and mode=="numeric": return "root"
    if kind=="root" and mode=="symbolic": return "equation"
    return kind

def _numbers(text: str, key: str):
    match=re.search(rf"{key}\s*=\s*\[([^]]+)\]",text,re.I)
    return [float(v.strip()) for v in match.group(1).split(",")] if match else []

def understand(query: str):
    q=query.lower().replace("＝","=")
    if "插值" in q: kind,tool,mode="interpolation","interpolate_data","numeric"
    elif "拟合" in q: kind,tool,mode="fit","fit_curve","numeric"
    elif "ode" in q or "初值" in q or "y'" in q: kind,tool,mode="ode","solve_ode","numeric"
    elif "求导" in q or "导数" in q: kind,tool,mode="derivative","differentiate_expression","symbolic"
    elif "积分" in q: kind,tool,mode="integral","integrate_expression","symbolic"
    elif "newton" in q or "数值" in q or "近似" in q or "验证失败重试" in q: kind,tool,mode="root","find_root","numeric"
    else: kind,tool,mode="equation","solve_symbolic_equation","symbolic"
    return {"kind":kind,"tool":tool,"mode":mode,"raw":query}

def expression_from(query: str, kind: str):
    q=query.replace("−","-").replace("＝","=")
    known=["cos(x) - x","cos(x)-x","x^2 - 2","x^2-2","sin(x)*exp(x)","y - t^2 + 1"]
    for item in known:
        if item.lower() in q.lower(): return item
    match=re.search(r"(?:求解|对|函数)\s*([a-zA-Z0-9_+*/^(). -]+?)(?:\s*=\s*0|\s*符号|\s*求导|，|,|$)",q)
    return match.group(1).strip() if match else "x^2-2"

def tool_args(state: ScientificAgentState):
    p=state["parsed_problem"]; q=state["user_query"]; kind=p["kind"]
    if kind=="root": return {"expression":expression_from(q,kind),"method":"newton","initial":0.5,"tolerance":state["tolerance"],"max_iterations":50}
    if kind=="equation": return {"expression":expression_from(q,kind),"variable":"x"}
    if kind=="derivative": return {"expression":expression_from(q,kind),"variable":"x","order":1}
    if kind=="integral": return {"expression":expression_from(q,kind),"variable":"x"}
    if kind in ("interpolation","fit"):
        x,y=_numbers(q,"x"),_numbers(q,"y")
        if not x: x=[0,1,2,3,4] if kind=="interpolation" else [0,1,2,3,4,5]
        if not y: y=[0,.8,.9,.1,-.8] if kind=="interpolation" else [1.1,2.8,7.2,12.9,21.2,30.8]
        return {"x":x,"y":y,**({"method":"cubic"} if kind=="interpolation" else {"degree":2})}
    return {"rhs":"y - t^2 + 1","y0":0.5,"t_span":[0.,2.]}

async def event(state,node,etype,title,summary,payload=None,duration=None):
    await store.emit(state["task_id"],etype,node,title,summary,payload,duration)

def model_for(state):
    return get_model(bool(state.get("use_local_model",True)))

async def model_failure(state,node,stage,exc):
    await event(state,node,"model_failed","本地模型调用失败",str(exc),{"stage":stage,"provider":state.get("model_provider")})
    if not settings.llm_fallback_to_mock: raise exc
    await event(state,node,"model_fallback","切换至 Mock 模式","已显式启用 LLM_FALLBACK_TO_MOCK",{"stage":stage})
    return MockModel()

async def understand_problem(state):
    await event(state,"understand_problem","node_started","开始理解问题","本地模型提取任务类型、变量和输出要求")
    fallback=understand(state["user_query"]); model=model_for(state)
    await event(state,"understand_problem","model_started","调用本地模型","生成可检查的结构化任务理解",{"provider":model.provider,"model":model.model,"stage":"understand"})
    try: parsed,record=await model.analyze(state["user_query"],fallback)
    except Exception as exc:
        model=await model_failure(state,"understand_problem","understand",exc); parsed,record=await model.analyze(state["user_query"],fallback)
    kind=parsed.get("kind",fallback["kind"])
    # Models sometimes use the broad "equation" label for numerical root
    # finding. Normalize that otherwise contradictory classification before
    # the registry binds it to an executable tool.
    model_mode=parsed.get("computation_mode",fallback["mode"])
    kind=normalize_kind(kind,model_mode,fallback["kind"])
    # The model classifies; the registry owns the executable mapping.
    parsed.update({"kind":kind,"tool":KIND_TOOL[kind],"mode":model_mode,"raw":state["user_query"]})
    await event(state,"understand_problem","model_completed","模型任务理解完成",record["response_summary"],record,record["duration_ms"])
    await event(state,"understand_problem","node_completed","问题理解完成",parsed.get("decision_summary",f"识别为{kind}任务"),parsed)
    return {"parsed_problem":parsed,"computation_mode":parsed["mode"],"status":"running","model_provider":model.provider,"model_calls":[*state.get("model_calls",[]),record]}

async def plan_solution(state):
    await event(state,"plan_solution","node_started","开始制定方案","本地模型从注册表选择工具；系统绑定独立验证方法")
    suggested=tool_args(state); kind=state["parsed_problem"]["kind"]; model=model_for(state)
    await event(state,"plan_solution","model_started","请求模型选择工具","向模型提供工具 JSON Schema，不允许任意代码",{"provider":model.provider,"model":model.model,"available_tools":list(TOOLS)})
    planning_context={**state['parsed_problem'],'previous_error':state.get('error'),
        'previous_verifications':state.get('verification_results',[]),'retry_count':state.get('retry_count',0)}
    try: choice,record=await model.select_tool(state["user_query"],planning_context,suggested)
    except Exception as exc:
        model=await model_failure(state,"plan_solution","tool_selection",exc); choice,record=await model.select_tool(state["user_query"],state["parsed_problem"],suggested)
    expected=KIND_TOOL[kind]
    if choice["name"]!=expected: raise ValueError(f"模型工具选择与任务类型不一致: {choice['name']}（期望 {expected}）")
    await event(state,"plan_solution","model_completed","模型已生成工具调用",choice["decision_summary"],{"model_call":record,"tool_call":{"name":choice["name"],"arguments":choice["arguments"]}},record["duration_ms"])
    args=validate_call(choice["name"],choice["arguments"])
    verification={"root":"方程残差","equation":"解析解代回","derivative":"有限差分抽样","interpolation":"节点误差","fit":"拟合指标","ode":"初值和离散残差","integral":"求导回查"}[kind]
    plan=[{"step":1,"id":choice.get("id","science_call"),"action":"模型请求工具调用","tool":choice["name"],"arguments":args,"decision_summary":choice["decision_summary"]},{"step":2,"action":"程序化验证","method":verification},{"step":3,"action":"模型生成教学解释"}]
    await event(state,"plan_solution","plan_created","计算计划已生成",f"选择 {plan[0]['tool']}；验证方式：{verification}",{"plan":plan})
    await event(state,"plan_solution","node_completed","方案制定完成","计划可供检查",{"plan":plan})
    return {"plan":plan,"requires_human_review":bool(state.get("requires_human_review") or state.get("teaching_mode")),"model_calls":[*state.get("model_calls",[]),record]}

async def human_review(state):
    if not state.get("requires_human_review"): return {}
    await event(state,"human_review","node_started","等待人工审核","工作流已安全暂停")
    await event(state,"human_review","human_review_required","需要批准计算计划","可批准、修改参数或终止",{"plan":state["plan"],"parsed_problem":state["parsed_problem"]})
    feedback=interrupt({"task_id":state["task_id"],"plan":state["plan"]})
    await event(state,"human_review","human_feedback_received","已收到人工反馈",f"操作：{feedback.get('action')}",feedback)
    if feedback.get("action")=="reject":
        return {"human_feedback":feedback,"status":"rejected","error":"用户终止任务"}
    updates={"human_feedback":feedback,"requires_human_review":False}
    params=feedback.get("parameters",{})
    if "tolerance" in params: updates["tolerance"]=max(1e-14,min(.1,float(params["tolerance"])))
    await event(state,"human_review","node_completed","人工审核完成","计划已批准，继续执行")
    return updates

async def execute_tools(state):
    await event(state,"execute_tools","node_started","开始科学计算","仅调用已注册的受控工具")
    call=state["plan"][0]; args=dict(call["arguments"]); args["tolerance"]=state["tolerance"] if call["tool"]=="find_root" else args.get("tolerance")
    if args.get("tolerance") is None: args.pop("tolerance",None)
    await event(state,"execute_tools","tool_started","工具调用开始",call["tool"],{"tool":call["tool"],"arguments":args})
    started=time.perf_counter()
    try:
        # Tool inputs are tightly bounded; direct invocation also keeps deterministic
        # classroom/test environments from depending on executor thread availability.
        result=TOOLS[call["tool"]](**args); duration=(time.perf_counter()-started)*1000
        record={"id":call.get("id","science_call"),"tool":call["tool"],"arguments":args,"result":result,"duration_ms":duration}
        await event(state,"execute_tools","tool_completed","工具调用完成",f"{call['tool']} 返回结构化结果",record,duration)
        await event(state,"execute_tools","node_completed","科学计算完成","结果等待程序化验证",duration=duration)
        return {"tool_calls":[*state.get("tool_calls",[]),record],"observations":[result],"status":"running","error":None}
    except Exception as exc:
        await event(state,"execute_tools","tool_failed","工具调用失败",str(exc),{"tool":call["tool"]})
        return {"status":"failed","error":str(exc)}

def verify(state):
    kind=state["parsed_problem"]["kind"]; result=state["observations"][-1]; tol=state["tolerance"]
    if kind=="root":
        args=state['tool_calls'][-1]['arguments']
        value=abs(float(safe_expression(args['expression']).evalf(subs={ALLOWED_NAMES[args.get('variable','x')]:result['root']})))
        name="equation_residual"; passed=value<=tol
    elif kind=="equation":
        expr=safe_expression(result["expression"]); x=ALLOWED_NAMES["x"]; vals=[abs(complex(expr.evalf(subs={x:safe_expression(s)}))) for s in result["solutions"]]; value=max(vals,default=math.inf); name="symbolic_substitution"; passed=value<=tol
    elif kind=="derivative":
        source=safe_expression(result["source"]); deriv=safe_expression(result["expression"]); x=ALLOWED_NAMES["x"]; f=sp.lambdify(x,source,"numpy"); d=sp.lambdify(x,deriv,"numpy"); h=1e-5; value=max(abs(float(d(a))-(float(f(a+h))-float(f(a-h)))/(2*h)) for a in (-.7,.2,1.1)); name="finite_difference_check"; passed=value<max(tol,1e-7)
    elif kind=="interpolation": value=result["node_error"]; name="interpolation_nodes"; passed=value<=tol
    elif kind=="fit": value=result["rmse"]; name="fit_metrics"; passed=math.isfinite(value)
    elif kind=="ode": value=max(result["initial_error"],result["max_discrete_residual"]); name="ode_residual"; passed=result["initial_error"]<=tol and math.isfinite(value)
    else:
        args=state['tool_calls'][-1]['arguments']; x=ALLOWED_NAMES[args.get('variable','x')]
        difference=sp.simplify(sp.diff(safe_expression(result['expression']),x)-safe_expression(args['expression']))
        value=0. if difference==0 else 1.; name="symbolic_antiderivative"; passed=difference==0
    if "验证失败重试" in state["user_query"] and state.get("retry_count",0)==0: passed=False; value=max(float(value),tol*100); name="intentional_demo_failure"
    return {"name":name,"passed":bool(passed),"value":float(value),"tolerance":tol,"explanation":"由独立程序检查生成；不是语言模型主观判断"}

async def verify_result(state):
    await event(state,"verify_result","node_started","开始验证结果","使用独立数值或符号检查")
    check=verify(state); await event(state,"verify_result","verification_completed","验证完成",("通过" if check["passed"] else "未通过")+f"：{check['name']} = {check['value']:.3g}",check)
    await event(state,"verify_result","node_completed","结果验证结束","验证证据已记录",check)
    return {"verification_results":[*state.get("verification_results",[]),check],"status":"verified" if check["passed"] else "verification_failed"}

async def retry(state):
    count=state.get("retry_count",0)+1; new_tol=max(state["tolerance"]*.01,1e-12)
    await event(state,"plan_solution","retry_started","验证失败，开始修正",f"第 {count} 次重试；收紧容差至 {new_tol:g}",{"retry_count":count,"tolerance":new_tol})
    return {"retry_count":count,"tolerance":new_tol,"status":"running"}

async def render_visualization(state):
    await event(state,"render_visualization","node_started","生成教学图表","将结果转换为 Plotly 数据")
    kind=state["parsed_problem"]["kind"]; artifact=plot_artifact(kind,state["observations"][-1]) if kind in ("root","interpolation","fit","ode") else {}
    artifacts=[artifact] if artifact else []
    if artifact: await event(state,"render_visualization","artifact_created","图表已生成",artifact["title"],artifact)
    await event(state,"render_visualization","node_completed","可视化完成",f"生成 {len(artifacts)} 个图表")
    return {"artifacts":artifacts}

async def explain_result(state):
    await event(state,"explain_result","node_started","生成教学解释","本地模型根据工具结果和验证证据组织说明")
    call=state["tool_calls"][-1]; check=state["verification_results"][-1]
    fallback=f"已使用 `{call['tool']}` 完成计算。\n\n**程序验证的结论**：{check['name']} = {check['value']:.6g}，容差 {check['tolerance']:.1e}，验证通过。\n\n**计算结果**：`{call['result']}`\n\n**方法说明**：模型负责工具选择，科学计算库负责计算，独立验证器负责判定。\n\n**误差与限制**：结论适用于给定表达式、数据、定义域和数值容差。"
    model=model_for(state); await event(state,"explain_result","model_started","调用本地模型生成解释","只提供已验证的结构化证据",{"provider":model.provider,"model":model.model,"stage":"explain"})
    try: answer,record=await model.explain({"query":state["user_query"],"tool_call":call,"verification":check},fallback)
    except Exception as exc:
        model=await model_failure(state,"explain_result","explain",exc); answer,record=await model.explain({},fallback)
    await event(state,"explain_result","model_completed","模型解释生成完成",record["response_summary"],record,record["duration_ms"])
    await event(state,"explain_result","node_completed","教学解释完成","解释与程序验证证据分离展示")
    return {"final_answer":answer,"model_calls":[*state.get("model_calls",[]),record]}

async def finalize(state):
    status="failed" if state.get("status") in ("failed","rejected","verification_failed") else "completed"
    etype="workflow_completed" if status=="completed" else "workflow_failed"
    await event(state,"finalize","node_started","汇总任务","保存结果、验证证据和工作流记录")
    await event(state,"finalize",etype,"工作流完成" if status=="completed" else "工作流停止",state.get("error") or ("结果已经程序验证" if status=="completed" else "重试次数已用尽"))
    return {"status":status,"final_answer":state.get("final_answer") or f"任务未完成：{state.get('error') or '验证未通过'}"}

async def after_review(s): return "finalize" if s.get("status")=="rejected" else "execute_tools"
async def after_execute(s):
    if s.get('status')=='failed': return 'retry' if s.get('retry_count',0)<s.get('max_retries',2) else 'finalize'
    return 'verify_result'
async def after_verify(s):
    if s["verification_results"][-1]["passed"]: return "render_visualization"
    return "retry" if s.get("retry_count",0)<s.get("max_retries",2) else "finalize"

builder=StateGraph(ScientificAgentState)
for name,fn in [("understand_problem",understand_problem),("plan_solution",plan_solution),("human_review",human_review),("execute_tools",execute_tools),("verify_result",verify_result),("retry",retry),("render_visualization",render_visualization),("explain_result",explain_result),("finalize",finalize)]: builder.add_node(name,fn)
builder.add_edge(START,"understand_problem"); builder.add_edge("understand_problem","plan_solution"); builder.add_edge("plan_solution","human_review")
builder.add_conditional_edges("human_review",after_review,{"finalize":"finalize","execute_tools":"execute_tools"})
builder.add_conditional_edges("execute_tools",after_execute,{"finalize":"finalize","verify_result":"verify_result","retry":"retry"})
builder.add_conditional_edges("verify_result",after_verify,{"render_visualization":"render_visualization","retry":"retry","finalize":"finalize"})
builder.add_edge("retry","plan_solution"); builder.add_edge("render_visualization","explain_result"); builder.add_edge("explain_result","finalize"); builder.add_edge("finalize",END)
graph=builder.compile(checkpointer=MemorySaver())

async def run_task(initial: ScientificAgentState | Command, task_id: str):
    config={"configurable":{"thread_id":task_id},"recursion_limit":30}
    try:
        async with asyncio.timeout(settings.llm_timeout_seconds*3+settings.timeout_seconds):
            async for state_update in graph.astream(initial,config=config,stream_mode='values'):
                store.save(task_id,dict(state_update))
        snapshot=graph.get_state(config)
        state=dict(snapshot.values)
        if snapshot.interrupts: state["status"]="waiting_review"
        store.save(task_id,state)
    except Exception as exc:
        current=store.get(task_id) or {"task_id":task_id}; current.update({"status":"failed","error":str(exc)})
        store.save(task_id,current); await store.emit(task_id,"workflow_failed",None,"工作流异常",str(exc))

async def resume_task(task_id: str, feedback: dict[str,Any]):
    await run_task(Command(resume=feedback),task_id)
