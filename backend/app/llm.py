from __future__ import annotations
import json, re, time
from typing import Any, Protocol
import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from .config import settings

TOOL_SPECS = [
 {"type":"function","function":{"name":"find_root","description":"求非线性标量方程 f(x)=0 的数值根；支持 Newton、二分和 Brent 方法。","strict":True,"parameters":{"type":"object","properties":{"expression":{"type":"string"},"variable":{"type":"string","enum":["x"]},"method":{"type":"string","enum":["newton","bisect","brentq"]},"initial":{"type":"number"},"bracket":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2},"tolerance":{"type":"number"},"max_iterations":{"type":"integer"}},"required":["expression","method","tolerance"],"additionalProperties":False}}},
 {"type":"function","function":{"name":"solve_symbolic_equation","description":"使用 SymPy 求单变量方程 expression=0 的解析解。","strict":True,"parameters":{"type":"object","properties":{"expression":{"type":"string"},"variable":{"type":"string","enum":["x","y","z","t"]}},"required":["expression","variable"],"additionalProperties":False}}},
 {"type":"function","function":{"name":"differentiate_expression","description":"对安全数学表达式进行符号求导。","strict":True,"parameters":{"type":"object","properties":{"expression":{"type":"string"},"variable":{"type":"string","enum":["x","y","z","t"]},"order":{"type":"integer","minimum":1,"maximum":5}},"required":["expression","variable","order"],"additionalProperties":False}}},
 {"type":"function","function":{"name":"integrate_expression","description":"计算安全数学表达式的不定积分。","strict":True,"parameters":{"type":"object","properties":{"expression":{"type":"string"},"variable":{"type":"string","enum":["x","y","z","t"]}},"required":["expression","variable"],"additionalProperties":False}}},
 {"type":"function","function":{"name":"interpolate_data","description":"对数据做线性插值或三次样条插值。","strict":True,"parameters":{"type":"object","properties":{"x":{"type":"array","items":{"type":"number"}},"y":{"type":"array","items":{"type":"number"}},"method":{"type":"string","enum":["linear","cubic"]},"samples":{"type":"integer"}},"required":["x","y","method"],"additionalProperties":False}}},
 {"type":"function","function":{"name":"fit_curve","description":"用最小二乘多项式拟合数据。","strict":True,"parameters":{"type":"object","properties":{"x":{"type":"array","items":{"type":"number"}},"y":{"type":"array","items":{"type":"number"}},"degree":{"type":"integer","minimum":1,"maximum":5}},"required":["x","y","degree"],"additionalProperties":False}}},
 {"type":"function","function":{"name":"solve_ode","description":"使用 SciPy 求一阶初值常微分方程 y'=f(t,y)。","strict":True,"parameters":{"type":"object","properties":{"rhs":{"type":"string"},"y0":{"type":"number"},"t_span":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2},"samples":{"type":"integer"}},"required":["rhs","y0","t_span"],"additionalProperties":False}}},
]
TOOL_NAMES={item["function"]["name"] for item in TOOL_SPECS}

class ModelAdapter(Protocol):
    provider: str
    model: str
    async def analyze(self, query: str, fallback: dict[str,Any]) -> tuple[dict[str,Any],dict[str,Any]]: ...
    async def select_tool(self, query: str, parsed: dict[str,Any], suggested: dict[str,Any]) -> tuple[dict[str,Any],dict[str,Any]]: ...
    async def explain(self, context: dict[str,Any], fallback: str) -> tuple[str,dict[str,Any]]: ...

def _record(stage: str, started: float, response: str, provider: str, model: str, fallback=False):
    return {"stage":stage,"provider":provider,"model":model,"duration_ms":(time.perf_counter()-started)*1000,
      "response_summary":response[:500],"fallback":fallback}

class MockModel:
    provider="mock"; model="deterministic-classroom-model"
    async def analyze(self,query,fallback):
        started=time.perf_counter(); data={**fallback,"decision_summary":f"识别任务并候选工具 {fallback['tool']}"}
        return data,_record("understand",started,data["decision_summary"],self.provider,self.model)
    async def select_tool(self,query,parsed,suggested):
        started=time.perf_counter(); call={"name":parsed["tool"],"arguments":suggested,"decision_summary":"根据任务类型和输入条件选择该受控工具"}
        return call,_record("tool_selection",started,call["decision_summary"],self.provider,self.model)
    async def explain(self,context,fallback):
        started=time.perf_counter(); return fallback,_record("explain",started,"生成分层教学解释",self.provider,self.model)

def _json_object(text: str) -> dict[str,Any]:
    cleaned=re.sub(r"<think>[\s\S]*?</think>","",text).strip()
    fenced=re.search(r"```(?:json)?\s*([\s\S]*?)```",cleaned)
    if fenced: cleaned=fenced.group(1).strip()
    start,end=cleaned.find("{"),cleaned.rfind("}")
    if start<0 or end<start: raise ValueError("模型没有返回 JSON 对象")
    return json.loads(cleaned[start:end+1])

class OpenAICompatibleModel:
    provider="local-vllm"
    def __init__(self):
        sync_http=httpx.Client(trust_env=False)
        async_http=httpx.AsyncClient(trust_env=False)
        self.model=settings.llm_model
        self.client=ChatOpenAI(model=settings.llm_model,api_key=settings.llm_api_key or "local",base_url=settings.llm_base_url,
          temperature=0,max_tokens=settings.llm_max_tokens,timeout=settings.llm_timeout_seconds,max_retries=1,
          http_client=sync_http,http_async_client=async_http,
          extra_body={"chat_template_kwargs":{"enable_thinking":False}})
    async def analyze(self,query,fallback):
        started=time.perf_counter()
        prompt=f"""分析下面的科学计算题，只输出一个 JSON 对象，不要输出推理过程。
允许的任务类型: root,equation,derivative,integral,interpolation,fit,ode。
字段: kind, computation_mode(symbolic或numeric), variables(字符串数组), constraints(字符串数组), expected_output, decision_summary(一句公开摘要), needs_clarification(布尔值)。
用户问题: {query}
规则路由给出的可靠候选: {json.dumps(fallback,ensure_ascii=False)}"""
        msg=await self.client.ainvoke([SystemMessage(content="你是科学计算 Agent 的任务分析器。不要泄露思维链，只给结构化结论。"),HumanMessage(content=prompt)])
        data=_json_object(str(msg.content)); data["tool"]=fallback["tool"]; data["raw"]=query
        return data,_record("understand",started,data.get("decision_summary","完成任务分类"),self.provider,self.model)
    async def select_tool(self,query,parsed,suggested):
        started=time.perf_counter(); bound=self.client.bind_tools(TOOL_SPECS,tool_choice="required")
        prompt=f"""你是科学计算 Agent。请调用且只调用一个最合适的工具，不要自己计算答案。
用户问题: {query}
任务分析: {json.dumps(parsed,ensure_ascii=False)}
精度参考: {suggested.get('tolerance',1e-8)}
从用户问题中提取表达式和数据，不得替换为预置示例。缺少非必要参数时使用工具默认值。"""
        msg: AIMessage=await bound.ainvoke([SystemMessage(content="必须通过提供的科学工具计算。不要输出 Python 代码。"),HumanMessage(content=prompt)])
        if len(msg.tool_calls)!=1: raise ValueError("模型必须生成且仅生成一个工具调用")
        item=msg.tool_calls[0]
        if item["name"] not in TOOL_NAMES: raise ValueError(f"模型选择了未注册工具: {item['name']}")
        args=item.get("args",{})
        call={"id":item.get("id") or "science_call","name":item["name"],"arguments":args,"decision_summary":f"模型请求调用 {item['name']}"}
        return call,_record("tool_selection",started,call["decision_summary"],self.provider,self.model)
    async def explain(self,context,fallback):
        started=time.perf_counter(); prompt=f"""根据以下已验证的结构化记录写中文教学解释。必须清楚分成“程序验证的结论”“方法说明”“误差与限制”。不得改变数值，不得宣称未验证内容已通过。不要展示思维链。
记录: {json.dumps(context,ensure_ascii=False,default=str)}"""
        call=context["tool_call"]; call_id=call.get("id","science_call")
        msg=await self.client.ainvoke([
            SystemMessage(content="你是科学计算课程助教，只解释工具结果和验证证据。"),
            HumanMessage(content=context["query"]),
            AIMessage(content="",tool_calls=[{"id":call_id,"name":call["tool"],"args":call["arguments"]}]),
            ToolMessage(content=json.dumps(call["result"],ensure_ascii=False),tool_call_id=call_id),
            HumanMessage(content=prompt)])
        answer=re.sub(r"<think>[\s\S]*?(?:</think>|$)","",str(msg.content)).strip() or fallback
        return answer,_record("explain",started,"根据程序验证证据生成教学解释",self.provider,self.model)

def get_model(use_local_model: bool=True) -> ModelAdapter:
    if not use_local_model or settings.llm_provider.lower()=="mock": return MockModel()
    return OpenAICompatibleModel()
