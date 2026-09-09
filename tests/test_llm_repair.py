import json
import httpx
import pytest
from app.verification.llm import LLMSettings,LLMRepairPolicy,LLMRepairError,build_system_prompt
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.io import load_snapshot
from app.verification.repair import repair_scene


def settings(**kwargs):
    return LLMSettings(_env_file=None,base_url='https://model.example/v1',model='test-model',api_key='unit-test-secret',**kwargs)


def scene():
    return load_snapshot('configs/geometry-diagnosis-demo.json')


def reply(content,finish='stop'):
    return httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':content}}],'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})


def test_system_prompt_builder_injects_request_limits():
    prompt=build_system_prompt(maximum_move_m=1.25,maximum_candidates=3)
    assert 'no longer than 1.25 meters' in prompt
    assert 'at most 3 move alternatives' in prompt
    assert 'Return only JSON: {"moves":[{"object_id":"id"' in prompt


def test_real_http_contract_and_guarded_loop():
    log=[]
    def handler(request):
        assert request.url=='https://model.example/v1/chat/completions'
        assert request.headers['authorization']=='Bearer unit-test-secret'
        assert log[-1][0]=='LLM_REQUEST'
        body=json.loads(request.content)
        assert body['response_format']=={'type':'json_object'}
        context=json.loads(body['messages'][1]['content'])
        moves=[m for d in context['diagnostics'] if d['status']=='fail' for m in d['suggestions']][:8]
        return reply(json.dumps({'moves':moves}))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        policy=LLMRepairPolicy(settings(),lambda k,p:log.append((k,p)),client)
        result=repair_scene(scene(),DeterministicGeometryVerifier(),policy)
    assert result.status=='pass' and len(result.actions)==3
    assert policy.calls==3
    assert 'unit-test-secret' not in json.dumps(log)
    assert log[-1][1]['usage']['total_tokens']==30


@pytest.mark.parametrize('content,finish',[
    ('not json','stop'),('{"moves":[],"status":"pass"}','stop'),
    ('{"moves":[{"object_id":"x","delta_m":[0,0,0],"diagnosis_id":"x","delete":true}]}','stop'),
    ('{"moves":[]}','length'),
])
def test_invalid_or_truncated_response_fails(content,finish):
    with httpx.Client(transport=httpx.MockTransport(lambda r:reply(content,finish))) as client:
        policy=LLMRepairPolicy(settings(),client=client)
        with pytest.raises(LLMRepairError):
            policy.propose(scene(),DeterministicGeometryVerifier().diagnose(scene()))


def test_http_error_no_silent_rule_fallback():
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401,json={'error':'bad key'}))) as client:
        with pytest.raises(LLMRepairError,match='llm_http_401'):
            repair_scene(scene(),DeterministicGeometryVerifier(),LLMRepairPolicy(settings(),client=client))


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout('timeout')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMRepairError,match='llm_timeout'):
            repair_scene(scene(),DeterministicGeometryVerifier(),LLMRepairPolicy(settings(),client=client))


def test_unconfigured_rejected():
    with pytest.raises(LLMRepairError,match='not_configured'):
        LLMRepairPolicy(LLMSettings(_env_file=None,base_url='',model=''))


def test_model_cannot_move_fixed_objects_or_invent_diagnosis():
    moves=[{'object_id':'pedestal','delta_m':[1,0,0],'diagnosis_id':'support:vase,pedestal'},
           {'object_id':'vase','delta_m':[0,0,0],'diagnosis_id':'invented'}]
    with httpx.Client(transport=httpx.MockTransport(lambda r:reply(json.dumps({'moves':moves})))) as client:
        result=repair_scene(scene(),DeterministicGeometryVerifier(),LLMRepairPolicy(settings(),client=client))
    assert result.status=='blocked' and not result.actions


def test_journal_failure_prevents_network():
    def emit(kind,payload):
        raise OSError('db down')
    def handler(request):
        pytest.fail('must persist before HTTP')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OSError):
            LLMRepairPolicy(settings(),emit,client).propose(scene(),DeterministicGeometryVerifier().diagnose(scene()))
