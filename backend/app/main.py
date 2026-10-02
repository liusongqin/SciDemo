from __future__ import annotations
import asyncio, json, math, re, uuid
from urllib.parse import urlparse
from typing import Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from .examples import EXAMPLES
from .models import (ConversationRename, DirectLoginRequest, HumanFeedback,
                     ScientificAgentState, TaskCreate)
from .auth import (COOKIE, cas_login_url, current_identity, direct_login,
                   direct_prelogin, require_identity, set_session_cookie,
                   validate_cas_ticket)
from .storage import store
from .workflow import run_task, resume_task
from .config import settings
import httpx

def json_safe(value: Any) -> Any:
    """Replace values forbidden by strict JSON before they reach Starlette."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(json_safe(content))


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app=FastAPI(title="SciDemo API",version="0.1.0",description="科学计算教学 Agent 工作流 API",lifespan=lifespan,
            default_response_class=SafeJSONResponse)
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_methods=["*"],allow_headers=["*"])
running_tasks: dict[str,asyncio.Task] = {}

def launch(task_id: str, coroutine):
    task=asyncio.create_task(coroutine); running_tasks[task_id]=task
    task.add_done_callback(lambda finished: running_tasks.pop(task_id,None) if running_tasks.get(task_id) is finished else None)

def authorize_task(request: Request,state: dict[str,Any]):
    identity=current_identity(request)
    if state.get("owner_id") and (not identity or state["owner_id"]!=identity["id"]): raise HTTPException(403,"无权访问该任务")

def compact_conversation(parent: dict[str,Any]) -> tuple[list[dict[str,Any]],str]:
    """Keep recent turns and a bounded summary instead of replaying full traces."""
    history=list(parent.get("conversation_history",[]))
    verified=[]
    checks={item.get("call_id"):item for item in parent.get("verification_results",[]) if item.get("passed")}
    for call in parent.get("tool_calls",[]):
        if call.get("id") in checks:
            verified.append({"tool":call.get("tool"),
              "arguments_summary":json.dumps(call.get("arguments",{}),ensure_ascii=False,default=str)[:800],
              "result_summary":json.dumps(call.get("result",{}),ensure_ascii=False,default=str)[:1200]})
    history.append({"user":parent.get("user_query","")[:1200],
                    "assistant":(parent.get("final_answer") or parent.get("error") or "")[:2000],
                    "verified":verified[:4],"status":parent.get("status")})
    summary=parent.get("conversation_summary","")
    if len(history)>4:
        older=history[:-4]
        additions=[f"用户：{item.get('user','')[:400]}\n结论：{item.get('assistant','')[:800]}" for item in older]
        summary=(summary+"\n"+"\n".join(additions)).strip()[-6000:]
        history=history[-4:]
    return history,summary

def conversation_title(query: str) -> str:
    text=re.sub(r"\s+"," ",query).strip()
    text=re.sub(r"^(?:请|帮我|麻烦|能否|请你)\s*", "", text)
    text=re.split(r"[。！？\n]|(?:，并且|，然后|，同时)",text,1)[0].strip(" ，。")
    return text if len(text)<=28 else text[:27]+"…"

@app.get("/api/health")
async def health(): return {"status":"ok","llm_provider":settings.llm_provider,"llm_model":settings.llm_model}

@app.get("/api/model/status")
async def model_status():
    if settings.llm_provider.lower()=="mock": return {"available":True,"provider":"mock","model":"deterministic-classroom-model",
      "external":{"configured":bool(settings.external_llm_model and settings.external_llm_api_key),"model":settings.external_llm_model,"base_url":settings.external_llm_base_url}}
    try:
        async with httpx.AsyncClient(timeout=3,trust_env=False) as client:
            response=await client.get(settings.llm_base_url.rstrip("/")+"/models",headers={"Authorization":f"Bearer {settings.llm_api_key or 'local'}"})
            response.raise_for_status(); models=[item.get("id") for item in response.json().get("data",[])]
        return {"available":True,"provider":"local-vllm","configured_model":settings.llm_model,"models":models,
                "external":{"configured":bool(settings.external_llm_model and settings.external_llm_api_key),"model":settings.external_llm_model,"base_url":settings.external_llm_base_url}}
    except Exception as exc:
        return {"available":False,"provider":"local-vllm","configured_model":settings.llm_model,"error":str(exc),
                "external":{"configured":bool(settings.external_llm_model and settings.external_llm_api_key),"model":settings.external_llm_model,"base_url":settings.external_llm_base_url}}

@app.get("/api/auth/me")
async def auth_me(request: Request,response: Response):
    identity=current_identity(request)
    if not identity:
        identity=store.create_identity(); set_session_cookie(response,store.create_session(identity["id"]))
    return {**identity,"login_enabled":settings.buaa_direct_auth}

@app.post("/api/auth/prelogin")
async def auth_prelogin(): return await direct_prelogin()

@app.post("/api/auth/direct-login")
async def auth_direct_login(body: DirectLoginRequest,request: Request,response: Response):
    verified=await direct_login(body.prelogin_id,body.student_id,body.password,body.captcha)
    identity=store.create_identity("student",verified["student_id"],verified["display_name"])
    store.delete_session(request.cookies.get(COOKIE)); set_session_cookie(response,store.create_session(identity["id"]))
    return {**identity,"login_enabled":settings.buaa_direct_auth}

@app.get("/api/auth/cas/login")
async def auth_cas_login(request: Request,origin: str|None=None):
    if not settings.buaa_direct_auth: raise HTTPException(503,"北航登录当前未启用")
    candidate=origin or settings.frontend_url
    parsed=urlparse(candidate)
    if parsed.scheme not in ("http","https") or not parsed.netloc: raise HTTPException(400,"无效的前端地址")
    service_url=f"{parsed.scheme}://{parsed.netloc}/api/auth/cas/callback"
    response=RedirectResponse(cas_login_url(service_url),status_code=302)
    response.set_cookie("scidemo_cas_service",service_url,max_age=600,httponly=True,samesite="lax",
                        secure=settings.session_cookie_secure,path="/")
    return response

@app.get("/api/auth/cas/callback")
async def auth_cas_callback(ticket: str,request: Request):
    if not settings.buaa_direct_auth: raise HTTPException(503,"北航登录当前未启用")
    service_url=request.cookies.get("scidemo_cas_service") or settings.cas_service_url
    verified=await validate_cas_ticket(ticket,service_url)
    identity=store.create_identity("student",verified["student_id"],verified["display_name"])
    store.delete_session(request.cookies.get(COOKIE))
    parsed=urlparse(service_url); frontend=f"{parsed.scheme}://{parsed.netloc}"
    response=RedirectResponse(frontend,status_code=302); set_session_cookie(response,store.create_session(identity["id"]))
    response.delete_cookie("scidemo_cas_service",path="/")
    return response

@app.post("/api/auth/logout")
async def auth_logout(request: Request,response: Response):
    store.delete_session(request.cookies.get(COOKIE)); response.delete_cookie(COOKIE,path="/")
    identity=store.create_identity(); set_session_cookie(response,store.create_session(identity["id"]))
    return {**identity,"login_enabled":settings.buaa_direct_auth}

@app.get("/api/conversations")
async def conversations(request: Request): return store.list_conversations(require_identity(request)["id"])

@app.post("/api/conversations",status_code=201)
async def create_conversation(request: Request): return store.create_conversation(require_identity(request)["id"])

@app.patch("/api/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str,body: ConversationRename,request: Request):
    if not store.rename_conversation(conversation_id,require_identity(request)["id"],body.title): raise HTTPException(404,"会话不存在")
    return {"conversation_id":conversation_id,"title":body.title.strip()}

@app.delete("/api/conversations/{conversation_id}",status_code=204)
async def delete_conversation(conversation_id: str,request: Request):
    if not store.delete_conversation(conversation_id,require_identity(request)["id"]): raise HTTPException(404,"会话不存在")
    return Response(status_code=204)

@app.get("/api/examples")
async def examples(): return EXAMPLES

@app.post("/api/tasks",status_code=202)
async def create_task(body: TaskCreate,request: Request,response: Response):
    task_id=str(uuid.uuid4())
    identity=current_identity(request)
    if not identity:
        identity=store.create_identity(); set_session_cookie(response,store.create_session(identity["id"]))
    parent=store.get(body.parent_task_id) if body.parent_task_id else None
    if body.parent_task_id and not parent: raise HTTPException(404,"上一轮任务不存在")
    if parent and parent.get("owner_id") and parent["owner_id"]!=identity["id"]: raise HTTPException(403,"无权继续该会话")
    conversation_id=(parent.get("conversation_id") if parent else body.conversation_id) or str(uuid.uuid4())
    owner=store.conversation_owner(conversation_id)
    if owner and owner!=identity["id"]: raise HTTPException(403,"无权访问该会话")
    store.touch_conversation(conversation_id,identity["id"],conversation_title(body.query))
    history,summary=compact_conversation(parent) if parent else ([],"")
    state: ScientificAgentState={"task_id":task_id,"conversation_id":conversation_id,
      "conversation_history":history,"conversation_summary":summary,
      "conversation_task_ids":([*parent.get("conversation_task_ids",[]),parent["task_id"]][-20:] if parent else []),
      "workflow_kind":"pending","owner_id":identity["id"],"user_query":body.query,"parsed_problem":{},"computation_mode":"",
      "plan":[],"current_step":0,"agent_steps":[],"pending_action":None,"review_completed":False,
      "active_agent":"problem_analyst","agent_handoffs":[],
      "tool_calls":[],"observations":[],"verification_results":[],"artifacts":[],"messages":[],"model_calls":[],
      "status":"queued","requires_human_review":body.require_review,"human_feedback":None,"retry_count":0,
      "max_retries":body.max_retries,"tolerance":body.tolerance,"teaching_mode":body.teaching_mode,
      "use_local_model":body.model_backend!="mock","model_backend":body.model_backend,
      "model_provider":{"local":"local-vllm","external":"external-api","mock":"mock"}[body.model_backend],"final_answer":None,"error":None}
    store.save(task_id,state)
    await store.emit(task_id,"workflow_started",None,"工作流已启动","已分配独立 thread_id",{"thread_id":task_id})
    launch(task_id,run_task(state,task_id))
    return {"task_id":task_id,"status":"queued"}

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str,request: Request):
    state=store.get(task_id)
    if not state: raise HTTPException(404,"任务不存在")
    authorize_task(request,state)
    return {"task":state,"events":store.events(task_id)}

@app.post("/api/tasks/{task_id}/review",status_code=202)
async def review(task_id: str, body: HumanFeedback,request: Request):
    state=store.get(task_id)
    if not state: raise HTTPException(404,"任务不存在")
    authorize_task(request,state)
    if state.get("status")!="waiting_review": raise HTTPException(409,"任务当前不在等待审核状态")
    launch(task_id,resume_task(task_id,body.model_dump()))
    return {"task_id":task_id,"status":"resuming"}

@app.post("/api/tasks/{task_id}/cancel",status_code=202)
async def cancel_task(task_id: str,request: Request):
    state=store.get(task_id)
    if not state: raise HTTPException(404,"任务不存在")
    authorize_task(request,state)
    if state.get("status") in ("completed","failed","rejected","cancelled"): raise HTTPException(409,"任务已经结束")
    task=running_tasks.get(task_id)
    if task and not task.done(): task.cancel()
    state.update({"status":"cancelled","error":"用户停止了模型生成"}); store.save(task_id,state)
    await store.emit(task_id,"workflow_cancelled",None,"已停止生成","当前模型调用和 Agent 工作流已取消")
    return {"task_id":task_id,"status":"cancelled"}

@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request, after: int=Query(default=0,ge=0)):
    state=store.get(task_id)
    if not state: raise HTTPException(404,"任务不存在")
    authorize_task(request,state)
    async def stream():
        last=after
        queue=store.subscribe(task_id)
        try:
            for item in store.events(task_id,last):
                last=item['sequence']; yield f"id: {last}\ndata: {json.dumps(json_safe(item),ensure_ascii=False,allow_nan=False)}\n\n"
            if (store.get(task_id) or {}).get('status') in ('completed','failed','cancelled'): return
            while True:
                try: item=await asyncio.wait_for(queue.get(),15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"; continue
                if item["sequence"]>last:
                    last=item["sequence"]; yield f"id: {last}\ndata: {json.dumps(json_safe(item),ensure_ascii=False,allow_nan=False)}\n\n"
                if item["event_type"] in ("workflow_completed","workflow_failed","workflow_cancelled"): break
        finally: store.unsubscribe(task_id,queue)
    return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
