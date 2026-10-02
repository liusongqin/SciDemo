import asyncio, math, uuid
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.types import Command
from app.main import app, running_tasks
from app.storage import store
from app import workflow
from app.workflow import run_task
from app.llm import public_text
from app.config import settings

def initial(query: str, review=False):
    task_id=str(uuid.uuid4())
    return {"task_id":task_id,"user_query":query,"parsed_problem":{},"computation_mode":"","plan":[],"current_step":0,
      "tool_calls":[],"observations":[],"verification_results":[],"artifacts":[],"messages":[],"status":"queued",
      "requires_human_review":review,"human_feedback":None,"retry_count":0,"max_retries":2,"tolerance":1e-9,
      "teaching_mode":False,"use_local_model":False,"final_answer":None,"error":None}

def test_fallback_answer_includes_every_verified_result():
    state=initial("多步计算")
    state["tool_calls"]=[
        {"id":"one","tool":"differentiate_expression","arguments":{"expression":"x^2"},"result":{"expression":"2*x"}},
        {"id":"two","tool":"evaluate_expression","arguments":{"expression":"x^2","values":{"x":2}},"result":{"value":4}},
    ]
    state["verification_results"]=[
        {"call_id":"one","passed":True,"name":"derivative","value":0.0},
        {"call_id":"two","passed":True,"name":"evaluation","value":4.0},
    ]
    answer=workflow.fallback_answer(state)
    assert "differentiate_expression" in answer and "evaluate_expression" in answer

@pytest.mark.asyncio
async def test_health_api():
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        assert (await client.get("/api/health")).json()["status"]=="ok"

@pytest.mark.asyncio
async def test_guest_can_create_and_list_empty_conversation():
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        identity=(await client.get("/api/auth/me")).json()
        created=(await client.post("/api/conversations")).json()
        conversations=(await client.get("/api/conversations")).json()
    assert identity["kind"]=="guest"
    assert any(item["conversation_id"]==created["conversation_id"] for item in conversations)

@pytest.mark.asyncio
async def test_guest_can_rename_and_delete_own_conversation():
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        await client.get("/api/auth/me")
        created=(await client.post("/api/conversations")).json(); conversation_id=created["conversation_id"]
        renamed=await client.patch(f"/api/conversations/{conversation_id}",json={"title":"极限计算"})
        deleted=await client.delete(f"/api/conversations/{conversation_id}")
        conversations=(await client.get("/api/conversations")).json()
    assert renamed.status_code==200 and renamed.json()["title"]=="极限计算"
    assert deleted.status_code==204
    assert not any(item["conversation_id"]==conversation_id for item in conversations)

@pytest.mark.asyncio
async def test_running_conversation_must_be_stopped_before_deletion():
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        identity=(await client.get("/api/auth/me")).json()
        created=(await client.post("/api/conversations")).json(); conversation_id=created["conversation_id"]
        state=initial("仍在运行的任务")
        state.update({"conversation_id":conversation_id,"owner_id":identity["id"],"status":"running"})
        store.save(state["task_id"],state)
        # Workflow state reducers must not be able to drop immutable ownership metadata.
        reduced={key:value for key,value in state.items() if key!="owner_id"}
        reduced["status"]="running"; store.save(state["task_id"],reduced)
        assert store.get(state["task_id"])["owner_id"]==identity["id"]
        restored=await client.get(f"/api/conversations/{conversation_id}")
        assert restored.status_code==200
        assert restored.json()["task"]["task_id"]==state["task_id"]
        assert restored.json()["task"]["status"]=="running"
        rejected=await client.delete(f"/api/conversations/{conversation_id}")
        assert rejected.status_code==409
        assert store.conversation_owner(conversation_id)==identity["id"]
        state["status"]="cancelled"; store.save(state["task_id"],state)
        deleted=await client.delete(f"/api/conversations/{conversation_id}")
    assert deleted.status_code==204
    assert store.conversation_owner(conversation_id) is None

@pytest.mark.asyncio
async def test_cas_login_uses_browser_origin_for_service_callback(monkeypatch):
    monkeypatch.setattr(settings, "buaa_direct_auth", True)
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test",follow_redirects=False) as client:
        response=await client.get("/api/auth/cas/login",params={"origin":"http://192.168.1.8:5173"})
    assert response.status_code==302
    assert "service=http%3A%2F%2F192.168.1.8%3A5173%2Fapi%2Fauth%2Fcas%2Fcallback" in response.headers["location"]
    assert "scidemo_cas_service=" in response.headers["set-cookie"]

@pytest.mark.asyncio
async def test_buaa_login_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "buaa_direct_auth", False)
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        identity=(await client.get("/api/auth/me")).json()
        response=await client.post("/api/auth/prelogin")
    assert identity["kind"]=="guest" and identity["login_enabled"] is False
    assert response.status_code==503

@pytest.mark.asyncio
async def test_cancel_running_generation():
    state=initial("取消一个正在生成的报告")
    state["status"]="running"; store.save(state["task_id"],state)
    sleeper=asyncio.create_task(asyncio.sleep(60)); running_tasks[state["task_id"]]=sleeper
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post(f"/api/tasks/{state['task_id']}/cancel")
    await asyncio.sleep(0)
    assert response.status_code==202
    assert sleeper.cancelled()
    assert store.get(state["task_id"])["status"]=="cancelled"
    assert store.events(state["task_id"])[-1]["event_type"]=="workflow_cancelled"

@pytest.mark.asyncio
async def test_task_api_serializes_non_finite_verification_values_as_null():
    state=initial("工具执行失败")
    state["verification_results"]=[{"passed":False,"value":math.inf}]
    store.save(state["task_id"],state)
    await store.emit(state["task_id"],"verification_completed","verify_result","验证失败","误差不可计算",{"value":math.nan})
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get(f"/api/tasks/{state['task_id']}")
    assert response.status_code==200
    data=response.json()
    assert data["task"]["verification_results"][0]["value"] is None
    assert data["events"][-1]["payload"]["value"] is None

@pytest.mark.asyncio
async def test_mock_end_to_end_and_event_history():
    state=initial("使用 Newton 法求解 cos(x)-x=0")
    await run_task(state,state["task_id"])
    data=store.get(state["task_id"]); events=store.events(state["task_id"])
    assert data["status"]=="completed" and data["verification_results"][-1]["passed"]
    assert data["artifacts"] and any(e["event_type"]=="tool_completed" for e in events)
    assert data["active_agent"]=="report_writer"
    assert [(item["from"],item["to"]) for item in data["agent_handoffs"]]==[
        ("problem_analyst","scientific_solver"),
        ("scientific_solver","verification_critic"),
        ("verification_critic","scientific_solver"),
        ("scientific_solver","report_writer"),
    ]
    assert len([e for e in events if e["event_type"]=="agent_handoff"])==4
    assert "verification_review" in [call["stage"] for call in data["model_calls"]]
    assert "synthesize" in [call["stage"] for call in data["model_calls"]]
    verification=data["verification_results"][-1]
    assert verification["program_passed"] is True
    assert verification["agent_approved"] is True
    assert verification["agent_review"]["recommendation"]=="accept_step"
    report_handoff=next(i for i,event in enumerate(events) if event["event_type"]=="agent_handoff" and event["payload"].get("to")=="report_writer")
    report_finished=next(i for i,event in enumerate(events) if event["event_type"]=="model_completed" and event["node"]=="report_writer")
    assert report_handoff < report_finished

@pytest.mark.asyncio
async def test_greeting_uses_general_route_without_science_agents():
    state=initial("你好呀")
    await run_task(state,state["task_id"])
    data=store.get(state["task_id"]); events=store.events(state["task_id"])
    assert data["status"]=="completed" and data["workflow_kind"]=="general"
    assert data["final_answer"] and not data.get("agent_handoffs")
    assert not any(item["event_type"]=="agent_handoff" for item in events)

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

@pytest.mark.asyncio
async def test_invalid_model_arguments_are_returned_for_self_correction(monkeypatch):
    class SelfCorrectingModel:
        provider="test"; model="self-correcting"
        async def analyze(self,query,fallback):
            record={"stage":"understand","provider":"test","model":self.model,"duration_ms":0,"response_summary":"ok"}
            return {**fallback,"decision_summary":"开始"},record
        async def decide(self,query,parsed,history,suggested):
            record={"stage":"agent_decision","provider":"test","model":self.model,"duration_ms":0,"response_summary":"next"}
            if not history:
                return {"action":"call_tool","id":"bad","name":"solve_symbolic_equation","arguments":{"expression":"这不是表达式","variable":"x"},"decision_summary":"首次尝试"},record
            if len(history)==1:
                assert history[0]["result"]["stage"]=="argument_validation"
                return {"action":"call_tool","id":"fixed","name":"solve_symbolic_equation","arguments":{"expression":"x^2-2","variable":"x"},"decision_summary":"修正参数"},record
            return {"action":"finish","answer":"修正后完成。","decision_summary":"结束"},record
    monkeypatch.setattr(workflow,"model_for",lambda state:SelfCorrectingModel())
    state=initial("求解方程")
    await run_task(state,state["task_id"])
    data=store.get(state["task_id"])
    assert data["status"]=="completed" and len(data["tool_calls"])==2
    assert data["tool_calls"][0]["result"]["stage"]=="argument_validation"

def test_public_text_removes_controller_wording():
    assert public_text("给用户展示已验证的插值结果")=="展示已验证的插值结果"
    assert public_text("我将调用插值工具")=="调用插值工具"

@pytest.mark.asyncio
async def test_auto_visualization_prevents_redundant_plot(monkeypatch):
    class RedundantPlotModel:
        provider="test"; model="redundant-plot"
        async def analyze(self,query,fallback):
            record={"stage":"understand","provider":"test","model":self.model,"duration_ms":0,"response_summary":"ok"}
            return {**fallback,"kind":"interpolation","computation_mode":"numeric","decision_summary":"插值"},record
        async def decide(self,query,parsed,history,suggested):
            record={"stage":"agent_decision","provider":"test","model":self.model,"duration_ms":0,"response_summary":"next"}
            if not history:
                return {"action":"call_tool","id":"interpolate","name":"interpolate_data","arguments":{"x":[0,1,2],"y":[0,1,0],"method":"cubic"},"decision_summary":"插值"},record
            return {"action":"call_tool","id":"bad_plot","name":"plot_function","arguments":{"expression":"x**2","start":0,"end":2,"variable":"x"},"decision_summary":"给用户展示图像"},record
        async def synthesize(self,query,history,reason):
            record={"stage":"synthesize","provider":"test","model":self.model,"duration_ms":0,"response_summary":"done"}
            return "插值与图表已完成。",record
    monkeypatch.setattr(workflow,"model_for",lambda state:RedundantPlotModel())
    state=initial("对 x=[0,1,2], y=[0,1,0] 三次样条插值并画图")
    await run_task(state,state["task_id"])
    data=store.get(state["task_id"])
    assert data["status"]=="completed"
    assert [call["tool"] for call in data["tool_calls"]]==["interpolate_data"]
    assert len(data["artifacts"])==1
    assert any(event["event_type"]=="redundant_action_skipped" for event in store.events(state["task_id"]))
