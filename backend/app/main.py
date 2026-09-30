from __future__ import annotations
import asyncio, json, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from .examples import EXAMPLES
from .models import HumanFeedback, ScientificAgentState, TaskCreate
from .storage import store
from .workflow import run_task, resume_task
from .config import settings
import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app=FastAPI(title="SciDemo API",version="0.1.0",description="科学计算教学 Agent 工作流 API",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_methods=["*"],allow_headers=["*"])

@app.get("/api/health")
async def health(): return {"status":"ok","llm_provider":settings.llm_provider,"llm_model":settings.llm_model}

@app.get("/api/model/status")
async def model_status():
    if settings.llm_provider.lower()=="mock": return {"available":True,"provider":"mock","model":"deterministic-classroom-model"}
    try:
        async with httpx.AsyncClient(timeout=3,trust_env=False) as client:
            response=await client.get(settings.llm_base_url.rstrip("/")+"/models",headers={"Authorization":f"Bearer {settings.llm_api_key or 'local'}"})
            response.raise_for_status(); models=[item.get("id") for item in response.json().get("data",[])]
        return {"available":True,"provider":"local-vllm","configured_model":settings.llm_model,"models":models}
    except Exception as exc:
        return {"available":False,"provider":"local-vllm","configured_model":settings.llm_model,"error":str(exc)}

@app.get("/api/examples")
async def examples(): return EXAMPLES

@app.post("/api/tasks",status_code=202)
async def create_task(body: TaskCreate):
    task_id=str(uuid.uuid4())
    state: ScientificAgentState={"task_id":task_id,"user_query":body.query,"parsed_problem":{},"computation_mode":"",
      "plan":[],"current_step":0,"agent_steps":[],"pending_action":None,"review_completed":False,
      "tool_calls":[],"observations":[],"verification_results":[],"artifacts":[],"messages":[],"model_calls":[],
      "status":"queued","requires_human_review":body.require_review,"human_feedback":None,"retry_count":0,
      "max_retries":body.max_retries,"tolerance":body.tolerance,"teaching_mode":body.teaching_mode,"use_local_model":body.use_local_model,
      "model_provider":"local-vllm" if body.use_local_model and settings.llm_provider.lower()!="mock" else "mock","final_answer":None,"error":None}
    store.save(task_id,state)
    await store.emit(task_id,"workflow_started",None,"工作流已启动","已分配独立 thread_id",{"thread_id":task_id})
    asyncio.create_task(run_task(state,task_id))
    return {"task_id":task_id,"status":"queued"}

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    state=store.get(task_id)
    if not state: raise HTTPException(404,"任务不存在")
    return {"task":state,"events":store.events(task_id)}

@app.post("/api/tasks/{task_id}/review",status_code=202)
async def review(task_id: str, body: HumanFeedback):
    state=store.get(task_id)
    if not state: raise HTTPException(404,"任务不存在")
    if state.get("status")!="waiting_review": raise HTTPException(409,"任务当前不在等待审核状态")
    asyncio.create_task(resume_task(task_id,body.model_dump()))
    return {"task_id":task_id,"status":"resuming"}

@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str, after: int=Query(default=0,ge=0)):
    if not store.get(task_id): raise HTTPException(404,"任务不存在")
    async def stream():
        last=after
        queue=store.subscribe(task_id)
        try:
            for item in store.events(task_id,last):
                last=item['sequence']; yield f"id: {last}\ndata: {json.dumps(item,ensure_ascii=False)}\n\n"
            if (store.get(task_id) or {}).get('status') in ('completed','failed'): return
            while True:
                try: item=await asyncio.wait_for(queue.get(),15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"; continue
                if item["sequence"]>last:
                    last=item["sequence"]; yield f"id: {last}\ndata: {json.dumps(item,ensure_ascii=False)}\n\n"
                if item["event_type"] in ("workflow_completed","workflow_failed"): break
        finally: store.unsubscribe(task_id,queue)
    return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
