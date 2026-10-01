import json
import httpx
import pytest
from langchain_openai import ChatOpenAI
from app.llm import DECISION_MAX_TOKENS, OpenAICompatibleModel, first_tool_call
from app.registry import validate_call
from app import workflow
from test_workflow import initial
from app.storage import store

@pytest.mark.asyncio
async def test_local_protocol_tool_result_round_trip(monkeypatch):
    requests=[]
    def endpoint(request):
        body=json.loads(request.content); requests.append(body)
        if len(requests)==1:
            message={'role':'assistant','content':json.dumps({'kind':'root','computation_mode':'numeric','decision_summary':'求三次方程的数值根'})}
        elif len(requests)==2:
            assert body['tools'] and body['tool_choice']=='required'
            message={'role':'assistant','content':None,'tool_calls':[{'id':'call_local_123','type':'function','function':{'name':'find_root','arguments':json.dumps({'expression':'x^3-7','method':'newton','initial':2.,'tolerance':1e-9})}}]}
        else:
            observation=next(m for m in body['messages'] if m['role']=='tool')
            assert observation['tool_call_id']=='call_local_123'
            assert abs(json.loads(observation['content'])['root']**3-7)<1e-8
            message={'role':'assistant','content':'已计算三次方程的根，程序验证通过。'}
        return httpx.Response(200,json={'id':'test_response','object':'chat.completion','created':1,'model':'test-local-protocol','choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if len(requests)==2 else 'stop'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        adapter=OpenAICompatibleModel()
        adapter.client=ChatOpenAI(model='test-local-protocol',api_key='local',base_url='http://local.test/v1',http_async_client=client,max_retries=0)
        monkeypatch.setattr(workflow,'model_for',lambda state:adapter)
        state=initial('Newton 求 x^3-7=0，初值2');state['use_local_model']=True
        await workflow.run_task(state,state['task_id'])
    result=store.get(state['task_id'])
    assert result['status']=='completed',result.get('error')
    assert len(requests)==3 and len(result['model_calls'])==3
    assert result['tool_calls'][0]['arguments']['expression']=='x^3-7'

def test_untrusted_tool_arguments():
    with pytest.raises(ValueError): validate_call('find_root',{'expression':'x','method':'newton','tolerance':1e-8,'file':'/tmp/a'})
    with pytest.raises(ValueError): validate_call('solve_ode',{'rhs':'y','y0':0,'t_span':[2,1]})
    with pytest.raises(ValueError): validate_call('matrix_calculation',{'operation':'inverse','matrix_a':[[1,2],[3]]})

def test_model_function_definition_is_normalized():
    result=validate_call('solve_symbolic_equation',{'expression':'f(x) = x^3 - 3*x + 1','variable':'x'})
    assert result['expression']=='x^3 - 3*x + 1'

def test_batched_model_tool_calls_are_executed_sequentially():
    first,deferred=first_tool_call([
        {'id':'one','name':'differentiate_expression','args':{}},
        {'id':'two','name':'plot_function','args':{}},
    ])
    assert first['id']=='one'
    assert deferred==1

def test_decision_output_budget_fits_local_context(monkeypatch):
    monkeypatch.setattr(workflow.settings,'llm_max_tokens',8192)
    adapter=OpenAICompatibleModel()
    assert adapter.client.max_tokens==DECISION_MAX_TOKENS

@pytest.mark.asyncio
async def test_model_failure_is_not_success(monkeypatch):
    class Unavailable:
        provider='local-vllm';model='unavailable'
        async def analyze(self,*args): raise ConnectionError('model unavailable')
    monkeypatch.setattr(workflow,'model_for',lambda state:Unavailable())
    monkeypatch.setattr(workflow.settings,'llm_fallback_to_mock',False)
    state=initial('Newton 求 cos(x)-x=0')
    await workflow.run_task(state,state['task_id'])
    assert store.get(state['task_id'])['status']=='failed'
    assert not any(e['event_type']=='tool_completed' for e in store.events(state['task_id']))
