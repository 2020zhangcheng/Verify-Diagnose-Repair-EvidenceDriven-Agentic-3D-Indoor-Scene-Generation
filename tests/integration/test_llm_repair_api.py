import json
from uuid import uuid4
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from app.db.postgres import SessionLocal
from app.verification.persistence import GeometryRunEvent
from app.verification.llm import LLMRepairPolicy

pytestmark=pytest.mark.integration
AUTH={'Authorization':'Bearer roomscout-local-demo'}


def body():
    return {'scene':json.loads(Path('configs/geometry-diagnosis-demo.json').read_text()),'max_iterations':5}


def configure(monkeypatch):
    monkeypatch.setenv('ROOMSCOUT_LLM_BASE_URL','https://unit.example/v1')
    monkeypatch.setenv('ROOMSCOUT_LLM_MODEL','test-model')
    monkeypatch.setenv('ROOMSCOUT_LLM_API_KEY','integration-secret')


def test_api_llm_event_first_idempotency_and_append_only(client,monkeypatch,tmp_path):
    from app.api import geometry
    configure(monkeypatch)
    calls=[]
    active_run=[]
    def handler(request):
        # The event is visible from another transaction BEFORE network dispatch.
        with SessionLocal() as session:
            event=session.scalar(select(GeometryRunEvent).where(GeometryRunEvent.type=='LLM_REQUEST',GeometryRunEvent.run_id==active_run[0]).order_by(GeometryRunEvent.sequence.desc()))
            assert event is not None
            assert event.payload['request']==json.loads(request.content)
        calls.append(request)
        context=json.loads(json.loads(request.content)['messages'][1]['content'])
        moves=[m for d in context['diagnostics'] if d['status']=='fail' for m in d['suggestions']][:8]
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'moves':moves})}}],'usage':{'total_tokens':30}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as provider:
        def factory(config,**kwargs):
            active_run.append(kwargs['emit'].__self__.run_id)
            return LLMRepairPolicy(config,client=provider,**kwargs)
        monkeypatch.setattr(geometry,'LLMRepairPolicy',factory)
        headers={**AUTH,'Idempotency-Key':str(uuid4())}
        response=client.post('/geometry/repair',headers=headers,json=body())
        assert response.status_code==200,response.text
        result=response.json()
        assert result['status']=='pass' and len(result['result']['actions'])==3
        assert len(calls)==3
        assert client.post('/geometry/repair',headers=headers,json=body()).json()==result
        assert len(calls)==3
        changed=body();changed['max_iterations']=1
        assert client.post('/geometry/repair',headers=headers,json=changed).status_code==409
    assert client.get('/geometry/repairs/'+result['run_id'],headers=AUTH).json()==result
    events=client.get(result['events_url'],headers=AUTH,params={'limit':200}).json()['items']
    assert 'integration-secret' not in json.dumps(events)
    assert sum(e['type']=='LLM_REQUEST' for e in events)==3
    from app.verification.replay import replay_journal
    journal=tmp_path/'events.jsonl'
    journal.write_text('\n'.join(json.dumps(e) for e in events))
    assert replay_journal(journal).model_dump(mode='json')==result['result']
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as session:
            session.execute(text('DELETE FROM geometry_run_events WHERE run_id=:id'),{'id':result['run_id']})
    assert client.get('/geometry/config').status_code==401
    config=client.get('/geometry/config',headers=AUTH).json()
    assert config['configured'] and 'api_key' not in config


def test_api_missing_config(client,monkeypatch):
    monkeypatch.setenv('ROOMSCOUT_LLM_BASE_URL','')
    monkeypatch.setenv('ROOMSCOUT_LLM_MODEL','')
    r=client.post('/geometry/repair',headers={**AUTH,'Idempotency-Key':str(uuid4())},json=body())
    assert r.status_code==503


def test_api_upstream_error_is_durable(client,monkeypatch):
    from app.api import geometry
    configure(monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={'error':'rate limited'}))) as provider:
        monkeypatch.setattr(geometry,'LLMRepairPolicy',lambda config,**kwargs:LLMRepairPolicy(config,client=provider,**kwargs))
        r=client.post('/geometry/repair',headers={**AUTH,'Idempotency-Key':str(uuid4())},json=body())
    assert r.status_code==502 and r.json()['error']=='llm_http_429'
    assert client.get('/geometry/repairs/'+r.json()['run_id'],headers=AUTH).json()['status']=='failed'
