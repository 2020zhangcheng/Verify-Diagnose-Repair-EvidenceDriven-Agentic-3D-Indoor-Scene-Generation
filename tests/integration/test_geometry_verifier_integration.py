import asyncio
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select
import pytest
from app.contracts.models import TaskSpec,SceneBelief,BeliefRef,RoomGeometry,CandidateLayout,GeometryEvidence,ArtifactRef,VerificationResult
from app.db.postgres import SessionLocal
from app.db.models.tables import EventRow
from app.db.repositories.store import locked_task,append_event,save_entity
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.io import load_snapshot
from app.verification.port import MEDIA_TYPE

pytestmark=pytest.mark.integration


def test_durable_verification_results_and_recompute(client,tmp_path):
    response=client.post('/tasks',headers={'Authorization':'Bearer roomscout-local-demo','Idempotency-Key':str(uuid4())},json={'request':'Diagnose geometry','auto_run':False})
    assert response.status_code==201
    task_id=response.json()['task_id']
    scene=load_snapshot('configs/geometry-diagnosis-demo.json')
    verifier=DeterministicGeometryVerifier()
    data=scene.model_dump_json().encode()
    path=tmp_path/'scene.json'
    path.write_bytes(data)
    digest=sha256(data).hexdigest()
    artifact=ArtifactRef(artifact_id=digest,sha256=digest,uri=path.as_uri(),media_type=MEDIA_TYPE)
    geometry=GeometryEvidence(environment_revision=scene.revision,status='available',artifacts=(artifact,))
    task=TaskSpec(task_id=task_id,objective='Diagnose geometry',constraints=(),seed=0,config_id='geometry-v1')
    belief=SceneBelief(ref=BeliefRef(belief_id='input',version=1),task_id=task_id,frame_id=scene.frame_id,environment_revision=scene.revision,room_geometry=RoomGeometry(),fusion_version='test')
    layout=CandidateLayout(layout_id='current-scene',task_id=task_id,belief_ref=belief.ref,placements=(),constraints=(),required_assumptions=(),planner_version='identity-test')
    with SessionLocal.begin() as session:
        row=append_event(session,locked_task(session,task_id),'TOOL_CALL',{'kind':'TOOL_CALL','operation_id':'diagnose','tool_name':'geometry.verify','arguments':{'scene':scene.model_dump(mode='json'),'config':verifier.config.model_dump(mode='json')}},'diagnose:call')
        call_id=row.id
    with SessionLocal() as session:
        assert session.get(EventRow,call_id)
    results=asyncio.run(verifier.verify(task,belief,layout,geometry))
    with SessionLocal.begin() as session:
        locked=locked_task(session,task_id)
        for i,result in enumerate(results):
            event_type='VERIFICATION_'+{'pass':'PASSED','fail':'FAILED','unknown':'UNKNOWN','error':'ERROR'}[result.status]
            save_entity(session,locked,result,event_type,f'diagnose:{i}')
        append_event(session,locked,'TOOL_RESULT',{'kind':'TOOL_RESULT','operation_id':'diagnose','status':'succeeded','result_entity_ids':[r.verification_id for r in results]},'diagnose:result')
    with SessionLocal() as session:
        events=list(session.scalars(select(EventRow).where(EventRow.task_id==task_id).order_by(EventRow.sequence)))
    recorded=tuple(VerificationResult.model_validate(e.payload['entity']) for e in events if e.type.startswith('VERIFICATION_'))
    assert recorded==results
    fresh=asyncio.run(verifier.verify(task,belief,layout,geometry))
    assert [r.model_dump(exclude={'checked_at'}) for r in recorded]==[r.model_dump(exclude={'checked_at'}) for r in fresh]
    assert {r.rule_id for r in results if r.status=='fail'}=={'collision','support'}
    assert next(e.sequence for e in events if e.id==call_id)<min(e.sequence for e in events if e.type.startswith('VERIFICATION_'))
