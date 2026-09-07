"""Adapter integration through existing durable events; no Graph protocol edits."""
import asyncio
import json
from uuid import uuid4
from pathlib import Path
from urllib.parse import unquote,urlparse
import pytest
from sqlalchemy import select
from app.db.postgres import SessionLocal
from app.db.models.tables import EventRow
from app.db.repositories.store import locked_task,append_event,save_entity
from app.environment.minimal import MinimalEnvironmentAdapter,load_scene
from app.contracts.models import Observation,OperationContext

pytestmark=pytest.mark.integration


def test_event_first_real_observation_roundtrip_and_restart(client,tmp_path):
    response=client.post('/tasks',headers={'Authorization':'Bearer roomscout-local-demo','Idempotency-Key':str(uuid4())},json={'request':'Environment integration','auto_run':False})
    assert response.status_code==201
    task_id=response.json()['task_id']
    scene=load_scene().model_copy(update={'width':17,'height':13})
    env=MinimalEnvironmentAdapter(task_id,tmp_path,scene)
    ctx=OperationContext(task_id=task_id,run_id='integration',operation_id='capture',expected_environment_revision=env.revision,fencing_token=1)
    with SessionLocal.begin() as session:
        task=locked_task(session,task_id)
        call=append_event(session,task,'TOOL_CALL',{'kind':'TOOL_CALL','operation_id':'capture','tool_name':'environment.observe',
            'arguments':{'environment_revision':env.revision,'scene':scene.model_dump(mode='json')}},'capture:call')
        call_id=call.id
    # Call has committed before any rendering; this is the application boundary.
    with SessionLocal() as session:
        assert session.get(EventRow,call_id) is not None
    obs=asyncio.run(env.observe(ctx))
    with SessionLocal.begin() as session:
        task=locked_task(session,task_id)
        save_entity(session,task,obs,'OBSERVATION_CREATED','capture:observation')
        append_event(session,task,'TOOL_RESULT',{'kind':'TOOL_RESULT','operation_id':'capture','status':'succeeded','result_entity_ids':[obs.observation_id]},'capture:result')
    with SessionLocal() as session:
        events=list(session.scalars(select(EventRow).where(EventRow.task_id==task_id).order_by(EventRow.sequence)))
        captured=next(e for e in events if e.type=='OBSERVATION_CREATED')
        restored=Observation.model_validate(captured.payload['entity'])
        assert restored==obs
        assert next(e.sequence for e in events if e.id==call_id)<captured.sequence
        for artifact in restored.artifacts:
            from hashlib import sha256
            assert sha256(Path(unquote(urlparse(artifact.uri).path)).read_bytes()).hexdigest()==artifact.sha256
    restarted=MinimalEnvironmentAdapter(task_id,tmp_path,scene)
    assert asyncio.run(restarted.observe(ctx))==restored
    assert asyncio.run(restarted.get_depth(restored))==restored.artifacts[1]
