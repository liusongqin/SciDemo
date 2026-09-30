from __future__ import annotations
import asyncio, math, re, time
from typing import Any
import sympy as sp
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from .config import settings
from .llm import MockModel, get_model
from .models import ScientificAgentState
from .registry import validate_call
from .science import ALLOWED_NAMES, TOOLS, plot_artifact, safe_expression
from .storage import store

KIND_TOOL={"root":"find_root","equation":"solve_symbolic_equation","derivative":"differentiate_expression",
 "integral":"integrate_expression","interpolation":"interpolate_data","fit":"fit_curve","ode":"solve_ode"}

def normalize_kind(kind:str,mode:str,fallback_kind:str)->str:
    if kind not in KIND_TOOL: kind=fallback_kind
    if kind=="equation" and mode=="numeric": return "root"
    if kind=="root" and mode=="symbolic": return "equation"
    return kind

def _numbers(text:str,key:str):
    match=re.search(rf"{key}\s*=\s*\[([^]]+)\]",text,re.I)
    return [float(v.strip()) for v in match.group(1).split(",")] if match else []

def understand(query:str):
    q=query.lower().replace("＝","=")
    if "插值" in q: kind,tool,mode="interpolation","interpolate_data","numeric"
    elif "拟合" in q: kind,tool,mode="fit","fit_curve","numeric"
    elif "ode" in q or "初值" in q or "y'" in q: kind,tool,mode="ode","solve_ode","numeric"
    elif "求导" in q or "导数" in q: kind,tool,mode="derivative","differentiate_expression","symbolic"
    elif "积分" in q: kind,tool,mode="integral","integrate_expression","symbolic"
    elif "newton" in q or "数值" in q or "近似" in q or "验证失败重试" in q: kind,tool,mode="root","find_root","numeric"
    else: kind,tool,mode="equation","solve_symbolic_equation","symbolic"
    return {"kind":kind,"tool":tool,"mode":mode,"raw":query}

def expression_from(query:str):
    q=query.replace("−","-").replace("＝","=")
    for item in ["cos(x) - x","cos(x)-x","x^2 - 2","x^2-2","sin(x)*exp(x)","y - t^2 + 1"]:
        if item.lower() in q.lower(): return item
    match=re.search(r"(?:求解|对|函数)\s*([a-zA-Z0-9_+*/^(). -]+?)(?:\s*=\s*0|\s*符号|\s*求导|，|,|$)",q)
    return match.group(1).strip() if match else "x^2-2"

def suggested_args(state:ScientificAgentState):
    kind=state["parsed_problem"]["kind"]; q=state["user_query"]
    if kind=="root": return {"expression":expression_from(q),"method":"newton","initial":.5,"tolerance":state["tolerance"],"max_iterations":50}
    if kind=="equation": return {"expression":expression_from(q),"variable":"x"}
    if kind=="derivative": return {"expression":expression_from(q),"variable":"x","order":1}
    if kind=="integral": return {"expression":expression_from(q),"variable":"x"}
    if kind in ("interpolation","fit"):
        x,y=_numbers(q,"x"),_numbers(q,"y")
        if not x: x=[0,1,2,3,4] if kind=="interpolation" else [0,1,2,3,4,5]
        if not y: y=[0,.8,.9,.1,-.8] if kind=="interpolation" else [1.1,2.8,7.2,12.9,21.2,30.8]
        return {"x":x,"y":y,**({"method":"cubic"} if kind=="interpolation" else {"degree":2})}
    return {"rhs":"y - t^2 + 1","y0":.5,"t_span":[0.,2.]}

async def event(state,node,etype,title,summary,payload=None,duration=None):
    await store.emit(state["task_id"],etype,node,title,summary,payload,duration)

def model_for(state): return get_model(bool(state.get("use_local_model",True)))

async def model_failure(state,node,stage,exc):
    await event(state,node,"model_failed","本地模型调用失败",str(exc),{"stage":stage,"provider":state.get("model_provider")})
    if not settings.llm_fallback_to_mock: raise exc
    await event(state,node,"model_fallback","切换至 Mock 模式","已显式启用 LLM_FALLBACK_TO_MOCK",{"stage":stage})
    return MockModel()

async def understand_problem(state):
    await event(state,"understand_problem","node_started","理解用户目标","提取计算对象、约束和预期输出")
    fallback=understand(state["user_query"]); model=model_for(state)
    await event(state,"understand_problem","model_started","模型分析问题","建立首轮任务上下文",{"provider":model.provider,"model":model.model})
    try: parsed,record=await model.analyze(state["user_query"],fallback)
    except Exception as exc:
        model=await model_failure(state,"understand_problem","understand",exc); parsed,record=await model.analyze(state["user_query"],fallback)
    kind=normalize_kind(parsed.get("kind",fallback["kind"]),parsed.get("computation_mode",fallback["mode"]),fallback["kind"])
    parsed.update({"kind":kind,"tool":KIND_TOOL[kind],"mode":parsed.get("computation_mode",fallback["mode"]),"raw":state["user_query"]})
    summary=parsed.get("decision_summary",f"识别为 {kind} 任务")
    step={"index":0,"type":"analysis","title":"理解用户目标","summary":summary,"status":"completed"}
    await event(state,"understand_problem","model_completed","问题理解完成",summary,parsed,record["duration_ms"])
    await event(state,"understand_problem","node_completed","初始分析已建立","控制权移交给 Agent 决策器")
    return {"parsed_problem":parsed,"computation_mode":parsed["mode"],"status":"running","model_provider":model.provider,
      "agent_steps":[*state.get("agent_steps",[]),step],"model_calls":[*state.get("model_calls",[]),record]}

def agent_history(state):
    checks=state.get("verification_results",[]); history=[]
    for call in state.get("tool_calls",[]):
        check=next((item for item in checks if item.get("call_id")==call.get("id")),{})
        history.append({"id":call.get("id"),"tool":call["tool"],"arguments":call["arguments"],"result":call.get("result",{}),
                        "verification":check})
    return history

def fallback_answer(state):
    passed=[x for x in agent_history(state) if x.get("verification",{}).get("passed")]
    if not passed: return "任务未能在允许的 Agent 步数内取得通过验证的结果。"
    item=passed[-1]; check=item["verification"]
    return f"已调用 `{item['tool']}` 完成计算。\n\n**程序验证的结论**：{check['name']} = {check['value']:.6g}，验证通过。\n\n**计算结果**：`{item['result']}`"

async def agent_decide(state):
    current=int(state.get("current_step",0))
    if current>=settings.max_tool_steps:
        answer=fallback_answer(state); ok=any(v.get("passed") for v in state.get("verification_results",[]))
        await event(state,"agent_decide","agent_limit_reached","达到 Agent 步数上限",f"最多允许 {settings.max_tool_steps} 次工具决策")
        return {"pending_action":{"action":"finish"},"final_answer":answer,"status":"running" if ok else "failed","error":None if ok else answer}
    await event(state,"agent_decide","node_started","Agent 正在决定下一步","读取最近的工具观察和验证证据")
    model=model_for(state); history=agent_history(state)
    try: action,record=await model.decide(state["user_query"],state["parsed_problem"],history,suggested_args(state))
    except Exception as exc:
        model=await model_failure(state,"agent_decide","agent_decision",exc); action,record=await model.decide(state["user_query"],state["parsed_problem"],history,suggested_args(state))
    if action.get("action")=="call_tool":
        action["arguments"]=validate_call(action["name"],action.get("arguments",{})); action.setdefault("id",f"science_call_{current+1}")
        title=f"决定调用 {action['name']}"
    elif action.get("action")=="finish":
        if not any(v.get("passed") for v in state.get("verification_results",[])): raise ValueError("Agent 不能在没有通过程序验证的结果时结束")
        title="决定结束任务"
    else: raise ValueError("模型返回了未知 Agent 动作")
    summary=action.get("decision_summary",title)
    step={"index":len(state.get("agent_steps",[])),"type":"decision","title":title,"summary":summary,"status":"completed","action":action.get("action"),"tool":action.get("name")}
    await event(state,"agent_decide","agent_decision",title,summary,{"action":action.get("action"),"tool":action.get("name"),"arguments":action.get("arguments",{})},record["duration_ms"])
    await event(state,"agent_decide","node_completed","Agent 决策完成",summary)
    updates={"pending_action":action,"current_step":current+(1 if action["action"]=="call_tool" else 0),
      "agent_steps":[*state.get("agent_steps",[]),step],"model_calls":[*state.get("model_calls",[]),record]}
    if action["action"]=="finish": updates["final_answer"]=action["answer"]
    else: updates["plan"]=[{"step":current+1,"action":"Agent 自主工具调用","tool":action["name"],"arguments":action["arguments"],"decision_summary":summary}]
    return updates

async def human_review(state):
    action=state.get("pending_action") or {}
    await event(state,"human_review","node_started","等待人工审核","Agent 循环已安全暂停")
    await event(state,"human_review","human_review_required","需要批准 Agent 动作",f"准备调用 {action.get('name')}",{"action":action})
    feedback=interrupt({"task_id":state["task_id"],"action":action})
    await event(state,"human_review","human_feedback_received","收到人工反馈",f"操作：{feedback.get('action')}",feedback)
    if feedback.get("action")=="reject": return {"human_feedback":feedback,"status":"rejected","error":"用户终止 Agent 任务","review_completed":True}
    updates={"human_feedback":feedback,"requires_human_review":False,"review_completed":True}
    if "tolerance" in feedback.get("parameters",{}): updates["tolerance"]=max(1e-14,min(.1,float(feedback["parameters"]["tolerance"])))
    await event(state,"human_review","node_completed","Agent 动作已批准","继续自主执行循环")
    return updates

async def execute_tool(state):
    action=state["pending_action"]; args=dict(action["arguments"])
    if action["name"]=="find_root": args["tolerance"]=min(float(args.get("tolerance",state["tolerance"])),state["tolerance"])
    await event(state,"execute_tool","node_started","执行 Agent 工具动作","调用服务器白名单中的科学工具")
    await event(state,"execute_tool","tool_started",f"调用 {action['name']}",action.get("decision_summary",""),{"tool":action["name"],"arguments":args,"call_id":action["id"]})
    started=time.perf_counter()
    try:
        result=TOOLS[action["name"]](**args); duration=(time.perf_counter()-started)*1000
        record={"id":action["id"],"tool":action["name"],"arguments":args,"result":result,"duration_ms":duration}
        step={"index":len(state.get("agent_steps",[])),"type":"tool","title":action["name"],"summary":"工具返回结构化观察","status":"completed","tool":action["name"]}
        await event(state,"execute_tool","tool_completed",f"{action['name']} 执行完成","结果已返回 Agent 环境",record,duration)
        await event(state,"execute_tool","node_completed","工具观察已记录","进入独立验证")
        return {"tool_calls":[*state.get("tool_calls",[]),record],"observations":[*state.get("observations",[]),result],"agent_steps":[*state.get("agent_steps",[]),step],"status":"running","error":None}
    except Exception as exc:
        duration=(time.perf_counter()-started)*1000; result={"error":str(exc)}
        record={"id":action["id"],"tool":action["name"],"arguments":args,"result":result,"duration_ms":duration}
        step={"index":len(state.get("agent_steps",[])),"type":"tool","title":action["name"],"summary":str(exc),"status":"failed","tool":action["name"]}
        await event(state,"execute_tool","tool_failed",f"{action['name']} 执行失败",str(exc),record,duration)
        return {"tool_calls":[*state.get("tool_calls",[]),record],"observations":[*state.get("observations",[]),result],"agent_steps":[*state.get("agent_steps",[]),step],"status":"tool_failed","error":str(exc)}

def verify_call(call,tolerance):
    name,args,result=call["tool"],call["arguments"],call["result"]
    passed,value,check_name=True,0.,"structural_check"
    if "error" in result: return {"call_id":call.get("id"),"name":"tool_execution","passed":False,"value":math.inf,"tolerance":tolerance,"explanation":"工具执行失败，交回 Agent 决定下一步"}
    if name=="find_root":
        x=ALLOWED_NAMES[args.get("variable","x")]; value=abs(float(safe_expression(args["expression"]).evalf(subs={x:result["root"]})))
        check_name,passed="equation_residual",value<=max(tolerance,float(args.get("tolerance",tolerance)))
    elif name=="solve_symbolic_equation":
        expr,x=safe_expression(result["expression"]),ALLOWED_NAMES[args.get("variable","x")]
        vals=[abs(complex(expr.evalf(subs={x:safe_expression(v)}))) for v in result["solutions"]]
        value,check_name,passed=max(vals,default=math.inf),"symbolic_substitution",max(vals,default=math.inf)<=tolerance
    elif name=="differentiate_expression":
        source,deriv,x=safe_expression(result["source"]),safe_expression(result["expression"]),ALLOWED_NAMES[args["variable"]]
        fn,df,h=sp.lambdify(x,source,"numpy"),sp.lambdify(x,deriv,"numpy"),1e-5
        value=max(abs(float(df(a))-(float(fn(a+h))-float(fn(a-h)))/(2*h)) for a in (-.7,.2,1.1)); check_name,passed="finite_difference_check",value<max(tolerance,1e-6)
    elif name=="integrate_expression":
        x=ALLOWED_NAMES[args["variable"]]; value=0. if sp.simplify(sp.diff(safe_expression(result["expression"]),x)-safe_expression(args["expression"]))==0 else 1.
        check_name,passed="symbolic_antiderivative",value==0
    elif name=="interpolate_data": value,check_name,passed=float(result["node_error"]),"interpolation_nodes",float(result["node_error"])<=tolerance
    elif name=="fit_curve": value,check_name,passed=float(result["rmse"]),"finite_fit_metrics",math.isfinite(float(result["rmse"]))
    elif name=="solve_ode":
        value=max(float(result["initial_error"]),float(result["max_discrete_residual"])); check_name,passed="ode_initial_and_residual",result["initial_error"]<=tolerance and math.isfinite(value)
    elif name=="numerical_integral": value,check_name,passed=float(result["difference"]),"quadrature_cross_check",float(result["difference"])<=max(tolerance*100,1e-5)
    elif name=="evaluate_expression": value,check_name,passed=abs(float(result["value"])),"finite_evaluation",math.isfinite(float(result["value"]))
    elif name=="simplify_expression":
        value=0. if sp.simplify(safe_expression(args["expression"])-safe_expression(result["expression"]))==0 else 1.; check_name,passed="symbolic_equivalence",value==0
    elif name=="series_expansion": value,check_name,passed=0.,"symbolic_series_generated",bool(result.get("expression"))
    elif name=="plot_function":
        count=len(result.get("data",[{}])[0].get("x",[])); value,check_name,passed=float(count),"finite_plot_samples",count>=2
    return {"call_id":call.get("id"),"name":check_name,"passed":bool(passed),"value":float(value),"tolerance":tolerance,"explanation":"由独立程序检查生成；Agent 不能修改该结论"}

async def verify_result(state):
    await event(state,"verify_result","node_started","独立验证工具观察","使用数值或符号程序检查")
    check=verify_call(state["tool_calls"][-1],state["tolerance"])
    if "验证失败重试" in state["user_query"] and state.get("retry_count",0)==0: check.update({"passed":False,"name":"intentional_demo_failure","value":max(check["value"],state["tolerance"]*100)})
    retry_count=state.get("retry_count",0)+(0 if check["passed"] else 1); title="验证通过" if check["passed"] else "验证未通过，交回 Agent"
    await event(state,"verify_result","verification_completed",title,f"{check['name']} = {check['value']:.3g}",check)
    if not check["passed"]:
        await event(state,"agent_decide","retry_started","验证失败，Agent 重新决策",f"第 {retry_count} 次修正；模型可更换工具或参数",{"retry_count":retry_count,"previous_tool":state["tool_calls"][-1]["tool"]})
    await event(state,"verify_result","node_completed","验证证据已记录","Agent 将读取该证据并自主决定下一步")
    step={"index":len(state.get("agent_steps",[])),"type":"verification","title":title,"summary":f"{check['name']} = {check['value']:.3g}","status":"completed" if check["passed"] else "failed"}
    return {"verification_results":[*state.get("verification_results",[]),check],"retry_count":retry_count,"agent_steps":[*state.get("agent_steps",[]),step],"status":"running"}

async def render_result(state):
    call=state["tool_calls"][-1]; result,name=call["result"],call["tool"]; artifact={}
    if name=="plot_function": artifact=result
    else:
        kind={"find_root":"root","interpolate_data":"interpolation","fit_curve":"fit","solve_ode":"ode"}.get(name)
        if kind: artifact=plot_artifact(kind,result)
    if not artifact: return {}
    await event(state,"render_result","artifact_created","Agent 观察图表已生成",artifact["title"],{**artifact,"source_tool":name})
    step={"index":len(state.get("agent_steps",[])),"type":"visualization","title":artifact["title"],"summary":f"由 {name} 的结构化结果生成","status":"completed"}
    return {"artifacts":[*state.get("artifacts",[]),artifact],"agent_steps":[*state.get("agent_steps",[]),step]}

async def finalize(state):
    failed=state.get("status") in ("failed","rejected") or not state.get("final_answer"); status="failed" if failed else "completed"
    await event(state,"finalize","node_started","Agent 汇总任务","保存动态步骤、观察与验证证据")
    await event(state,"finalize","workflow_failed" if failed else "workflow_completed","Agent 任务停止" if failed else "Agent 自主任务完成",state.get("error") or "模型已根据通过验证的观察决定结束")
    step={"index":len(state.get("agent_steps",[])),"type":"finish","title":"任务完成" if not failed else "任务停止","summary":state.get("error") or "Agent 已结束循环","status":"failed" if failed else "completed"}
    return {"status":status,"agent_steps":[*state.get("agent_steps",[]),step],"final_answer":state.get("final_answer") or f"任务未完成：{state.get('error') or '没有可验证结果'}"}

async def route_decision(state):
    if state.get("status")=="failed" or (state.get("pending_action") or {}).get("action")=="finish": return "finalize"
    if state.get("requires_human_review") and not state.get("review_completed"): return "human_review"
    return "execute_tool"
async def route_review(state): return "finalize" if state.get("status")=="rejected" else "execute_tool"
async def route_execute(state): return "agent_decide" if state.get("status")=="tool_failed" else "verify_result"

builder=StateGraph(ScientificAgentState)
for name,fn in [("understand_problem",understand_problem),("agent_decide",agent_decide),("human_review",human_review),("execute_tool",execute_tool),("verify_result",verify_result),("render_result",render_result),("finalize",finalize)]: builder.add_node(name,fn)
builder.add_edge(START,"understand_problem"); builder.add_edge("understand_problem","agent_decide")
builder.add_conditional_edges("agent_decide",route_decision,{"finalize":"finalize","human_review":"human_review","execute_tool":"execute_tool"})
builder.add_conditional_edges("human_review",route_review,{"finalize":"finalize","execute_tool":"execute_tool"})
builder.add_conditional_edges("execute_tool",route_execute,{"agent_decide":"agent_decide","verify_result":"verify_result"})
builder.add_edge("verify_result","render_result"); builder.add_edge("render_result","agent_decide"); builder.add_edge("finalize",END)
graph=builder.compile(checkpointer=MemorySaver())

async def run_task(initial:ScientificAgentState|Command,task_id:str):
    config={"configurable":{"thread_id":task_id},"recursion_limit":settings.max_tool_steps*5+20}
    timeout=max(settings.timeout_seconds,settings.llm_timeout_seconds*(min(settings.max_tool_steps,4)+1))
    try:
        async with asyncio.timeout(timeout):
            async for update in graph.astream(initial,config=config,stream_mode="values"): store.save(task_id,dict(update))
        snapshot=graph.get_state(config); state=dict(snapshot.values)
        if snapshot.interrupts: state["status"]="waiting_review"
        store.save(task_id,state)
    except Exception as exc:
        current=store.get(task_id) or {"task_id":task_id}; current.update({"status":"failed","error":str(exc)}); store.save(task_id,current)
        await store.emit(task_id,"workflow_failed",None,"Agent 工作流异常",str(exc))

async def resume_task(task_id:str,feedback:dict[str,Any]): await run_task(Command(resume=feedback),task_id)
