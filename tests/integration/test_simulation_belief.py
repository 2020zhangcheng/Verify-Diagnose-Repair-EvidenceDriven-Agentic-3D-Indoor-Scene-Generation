import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db.postgres import SessionLocal
from app.db.models.tables import EventRow
from app.db.repositories.store import locked_task, append_event, save_entity
from app.environment.minimal import MinimalEnvironmentAdapter, load_scene
from app.perception.simulation import SimulationPerception
from app.scene.belief import InMemoryObservationArchive, StructuredSceneBeliefService
from app.scene.replay import replay_beliefs
from app.contracts.models import OperationContext, TaskSpec, Observation

pytestmark = pytest.mark.integration


def test_durable_simulation_perception_belief_replay(client,tmp_path):
    response = client.post('/tasks',headers={'Authorization':'Bearer roomscout-local-demo','Idempotency-Key':str(uuid4())},json={'request':'Inspect room','auto_run':False})
    assert response.status_code == 201
    task_id = response.json()['task_id']
    env = MinimalEnvironmentAdapter(task_id,tmp_path/'env',load_scene().model_copy(update={'width':48,'height':36}))
    perception = SimulationPerception(env.scene,tmp_path/'perception')
    archive = InMemoryObservationArchive()
    service = StructuredSceneBeliefService(archive.load)
    spec = TaskSpec(task_id=task_id,objective='Inspect room',constraints=(),seed=0,config_id='scene-belief-v1')
    def save(entity,kind,key):
        with SessionLocal.begin() as session:
            save_entity(session,locked_task(session,task_id),entity,kind,key)
    def call(name,key,args):
        with SessionLocal.begin() as session:
            append_event(session,locked_task(session,task_id),'TOOL_CALL',{'kind':'TOOL_CALL','operation_id':key,'tool_name':name,'arguments':args},key+':call')
    def ctx(key):
        return OperationContext(task_id=task_id,run_id='integration',operation_id=key,expected_environment_revision=env.revision,fencing_token=1)
    save(spec,'TASK_UNDERSTOOD','spec')
    previous = None
    expected = []
    for index in range(2):
        if index:
            pose = raw.camera_pose.model_copy(update={'position_m':(1.5,-2,1.5)})
            call('environment.move_camera','move',pose.model_dump(mode='json'))
            assert asyncio.run(env.move_camera(pose,ctx('move'))).status == 'succeeded'
        key = f'capture-{index}'
        call('environment.observe',key,{'scene':env.scene.model_dump(mode='json')})
        raw = asyncio.run(env.observe(ctx(key)))
        save(raw,'OBSERVATION_CREATED',key+':raw')
        call('perception.simulation',key+':perception',{'raw_observation_id':raw.observation_id,'config':perception.config.model_dump(mode='json')})
        perceived = asyncio.run(perception.interpret(raw))
        save(perceived,'OBSERVATION_CREATED',key+':perceived')
        # Fusion input is deserialized from committed PostgreSQL events.
        with SessionLocal() as session:
            rows = list(session.scalars(select(EventRow).where(EventRow.task_id==task_id).order_by(EventRow.sequence)))
            restored = Observation.model_validate(next(e.payload['entity'] for e in rows if e.type=='OBSERVATION_CREATED' and e.payload['entity']['observation_id']==perceived.observation_id))
        archive.add(restored)
        call('scene.update',key+':fusion',{'fusion_config':service.config.model_dump(mode='json'),'fusion_version':service.config.version})
        previous = asyncio.run(service.update(previous,(restored,),spec))
        save(previous,'BELIEF_UPDATED',key+':belief')
        expected.append(previous)
    with SessionLocal() as session:
        events = [{'sequence':e.sequence,'type':e.type,'payload':e.payload} for e in session.scalars(select(EventRow).where(EventRow.task_id==task_id).order_by(EventRow.sequence))]
    assert asyncio.run(replay_beliefs(events)) == tuple(expected)
    assert 'radiator' not in {o.object_id for o in expected[0].objects}
    assert 'radiator' in {o.object_id for o in expected[1].objects}
