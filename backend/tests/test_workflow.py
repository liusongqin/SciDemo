import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.types import Command
from app.main import app
from app.storage import store
from app import workflow
from app.workflow import run_task

def initial(query: str, review=False):
    task_id=str(uuid.uuid4())
    return {"task_id":task_id,"user_query":query,"parsed_problem":{},"computation_mode":"","plan":[],"current_step":0,
      "tool_calls":[],"observations":[],"verification_results":[],"artifacts":[],"messages":[],"status":"queued",
      "requires_human_review":review,"human_feedback":None,"retry_count":0,"max_retries":2,"tolerance":1e-9,
      "teaching_mode":False,"use_local_model":False,"final_answer":None,"error":None}

@pytest.mark.asyncio
async def test_health_api():
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        assert (await client.get("/api/health")).json()["status"]=="ok"

@pytest.mark.asyncio
async def test_mock_end_to_end_and_event_history():
    state=initial("使用 Newton 法求解 cos(x)-x=0")
    await run_task(state,state["task_id"])
    data=store.get(state["task_id"]); events=store.events(state["task_id"])
    assert data["status"]=="completed" and data["verification_results"][-1]["passed"]
    assert data["artifacts"] and any(e["event_type"]=="tool_completed" for e in events)

@pytest.mark.asyncio
async def test_verification_failure_retries():
    state=initial("演示验证失败重试：用很低精度求 cos(x)-x=0，再自动提高精度")
    await run_task(state,state["task_id"]); data=store.get(state["task_id"])
    assert data["status"]=="completed" and data["retry_count"]==1
    assert any(e["event_type"]=="retry_started" for e in store.events(state["task_id"]))

@pytest.mark.asyncio
async def test_human_review_pause_resume_same_thread():
    state=initial("求解 x^2-2=0",True)
    await run_task(state,state["task_id"])
    assert store.get(state["task_id"])["status"]=="waiting_review"
    await run_task(Command(resume={"action":"approve","parameters":{}}),state["task_id"])
    assert store.get(state["task_id"])["status"]=="completed"

@pytest.mark.asyncio
async def test_agent_can_choose_multiple_tools(monkeypatch):
    class MultiStepModel:
        provider="test"; model="multi-step-controller"
        async def analyze(self,query,fallback):
            record={"stage":"understand","provider":"test","model":self.model,"duration_ms":0,"response_summary":"ok"}
            return {**fallback,"decision_summary":"需要分两步计算"},record
        async def decide(self,query,parsed,history,suggested):
            record={"stage":"agent_decision","provider":"test","model":self.model,"duration_ms":0,"response_summary":"next"}
            if not history:
                return {"action":"call_tool","id":"step_1","name":"simplify_expression","arguments":{"expression":"x+x"},"decision_summary":"先化简"},record
            if len(history)==1:
                return {"action":"call_tool","id":"step_2","name":"evaluate_expression","arguments":{"expression":"2*x","values":{"x":3}},"decision_summary":"再计算数值"},record
            return {"action":"finish","answer":"两步工具计算完成并通过验证。","decision_summary":"证据充分，结束"},record
    monkeypatch.setattr(workflow,"model_for",lambda state:MultiStepModel())
    state=initial("先化简 x+x，再计算 x=3 时的值")
    await run_task(state,state["task_id"])
    data=store.get(state["task_id"])
    assert data["status"]=="completed"
    assert [call["tool"] for call in data["tool_calls"]]==["simplify_expression","evaluate_expression"]
    assert len([event for event in store.events(state["task_id"]) if event["event_type"]=="agent_decision"])==3
