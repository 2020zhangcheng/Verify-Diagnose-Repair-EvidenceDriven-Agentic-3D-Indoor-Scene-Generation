"""Authenticated standalone LLM geometry experiments, separate from Fake Graph tasks."""
from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter,Depends,Header,HTTPException,Query
from fastapi.responses import JSONResponse
from pydantic import Field,field_validator
from sqlalchemy import select,text
from app.api.tasks import identity
from app.contracts.models import Contract
from app.verification.models import SceneSnapshot
from app.verification.io import parse_snapshot
from app.verification.geometry import DeterministicGeometryVerifier,load_config
from app.verification.llm import LLMSettings,LLMRepairPolicy,LLMRepairError
from app.verification.repair import repair_scene
from app.verification.persistence import GeometryRun,GeometryRunEvent,DatabaseJournal
from app.db.postgres import SessionLocal
from app.db.repositories.store import digest

router=APIRouter(prefix='/geometry',tags=['Geometry LLM Repair'])


class RepairRequest(Contract):
    scene: SceneSnapshot
    max_iterations: int = Field(default=5,ge=1,le=20)

    @field_validator('scene',mode='before')
    @classmethod
    def scene_input(cls,value):
        if isinstance(value,SceneSnapshot):
            return value
        try:
            return parse_snapshot(value)
        except (KeyError,TypeError) as exc:
            raise ValueError('Scene requires objects with object_id, position_m and size_m') from exc

    @field_validator('scene')
    @classmethod
    def scene_limit(cls,value):
        if len(value.objects)>100:
            raise ValueError('maximum 100 objects per synchronous experiment')
        return value


def model_settings():
    try:
        return LLMSettings()
    except ValueError:
        raise HTTPException(503,'llm_configuration_invalid') from None


def response(run):
    return {'run_id':run.id,'status':run.status,'result':run.result,'error':run.error,
            'events_url':f'/geometry/repairs/{run.id}/events'}


@router.get('/config')
def config(user=Depends(identity)):
    settings=model_settings()
    return {'configured':settings.configured,**settings.public(),'api_key_configured':bool(settings.api_key.get_secret_value())}


@router.post('/repair')
def repair(body:RepairRequest,idempotency_key:Annotated[str,Header(min_length=1,max_length=200)],user=Depends(identity)):
    config=model_settings()
    request_hash=digest(body.model_dump(mode='json'))
    with SessionLocal.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,1))'),{'scope':f'geometry:{user}:{idempotency_key}'})
        old=session.scalar(select(GeometryRun).where(GeometryRun.user_id==user,GeometryRun.idempotency_key==idempotency_key))
        if old:
            if old.request_hash!=request_hash:
                raise HTTPException(409,'idempotency_conflict')
            return response(old)
        if not config.configured:
            raise HTTPException(503,'llm_not_configured')
        run=GeometryRun(id=str(uuid4()),user_id=user,idempotency_key=idempotency_key,request_hash=request_hash,status='running',sequence=1)
        session.add(run)
        session.flush()
        session.add(GeometryRunEvent(run_id=run.id,sequence=1,type='USER_REQUEST',payload=body.model_dump(mode='json')))
        run_id=run.id
    journal=DatabaseJournal(run_id)
    verifier=DeterministicGeometryVerifier(load_config().model_copy(update={'maximum_iterations':body.max_iterations}))
    journal.emit('CONFIG',{'config':verifier.config.model_dump(mode='json'),'version':verifier.version,'policy':'llm-repair-v1','llm':config.public()})
    policy=LLMRepairPolicy(config,emit=journal.emit,maximum_move_m=verifier.config.maximum_move_m)
    try:
        result=repair_scene(body.scene,verifier,policy,journal.emit)
    except LLMRepairError as exc:
        journal.emit('RUN_FAILED',{'code':str(exc)})
        with SessionLocal.begin() as session:
            run=session.get(GeometryRun,run_id)
            run.status='failed'
            run.error=str(exc)
            data=response(run)
        return JSONResponse(status_code=502,content=data)
    journal.emit('FINAL_RESULT',result.model_dump(mode='json'))
    with SessionLocal.begin() as session:
        run=session.get(GeometryRun,run_id)
        run.status=result.status
        run.result=result.model_dump(mode='json')
        return response(run)


def owned(session,run_id,user):
    run=session.scalar(select(GeometryRun).where(GeometryRun.id==run_id,GeometryRun.user_id==user))
    if run is None:
        raise HTTPException(404,'geometry_run_not_found')
    return run


@router.get('/repairs/{run_id}')
def get_run(run_id:str,user=Depends(identity)):
    with SessionLocal() as session:
        return response(owned(session,run_id,user))


@router.get('/repairs/{run_id}/events')
def events(run_id:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200),user=Depends(identity)):
    with SessionLocal() as session:
        owned(session,run_id,user)
        rows=list(session.scalars(select(GeometryRunEvent).where(GeometryRunEvent.run_id==run_id,GeometryRunEvent.sequence>after).order_by(GeometryRunEvent.sequence).limit(limit+1)))
        return {'items':[{'sequence':r.sequence,'type':r.type,'payload':r.payload} for r in rows[:limit]],
                'next_cursor':rows[limit-1].sequence if len(rows)>limit else None}
