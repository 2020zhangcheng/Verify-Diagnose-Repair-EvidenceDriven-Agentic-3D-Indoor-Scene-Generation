"""Authenticated standalone LLM geometry experiments, separate from Fake Graph tasks."""
from typing import Annotated
from fastapi import APIRouter,Depends,Header,HTTPException,Query
from fastapi.responses import JSONResponse
from pydantic import Field,field_validator
from app.api.auth import identity
from app.contracts.models import Contract
from app.verification.models import SceneSnapshot
from app.verification.io import parse_snapshot
from app.verification.geometry import DeterministicGeometryVerifier,load_config
from app.verification.llm import LLMSettings,LLMRepairPolicy,LLMRepairError
from app.verification.repair import repair_scene
from app.verification.memory_persistence import InMemoryGeometryJournal,InMemoryGeometryStore,digest
from app.repair.graph import run_repair_loop
from app.repair.registry import default_repair_tool_registry
from app.repair.router import GeometryRepairRouter

router=APIRouter(prefix='/geometry',tags=['Geometry LLM Repair'])
geometry_store=InMemoryGeometryStore()


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


class ReactRepairRequest(RepairRequest):
    """Bounded ReAct request: at most ten tool/verify iterations."""

    max_iterations: int = Field(default=10,ge=1,le=10)


def model_settings():
    try:
        return LLMSettings()
    except ValueError:
        raise HTTPException(503,'llm_configuration_invalid') from None


def response(run):
    return {'run_id':run.id,'status':run.status,'result':run.result,'error':run.error,
            'events_url':f'/geometry/repairs/{run.id}/events'}


def open_run(body, user, idempotency_key, config):
    """Get or create a process-local run while preserving idempotency semantics."""
    request_payload=body.model_dump(mode='json')
    request_hash=digest(request_payload)
    old=geometry_store.find_by_key(user,idempotency_key)
    if old:
        if old.request_hash!=request_hash:
            raise HTTPException(409,'idempotency_conflict')
        return old,False
    if not config.configured:
        raise HTTPException(503,'llm_not_configured')
    try:
        return geometry_store.create(user,idempotency_key,request_hash,request_payload),True
    except ValueError as exc:
        # Another request may have won the in-process lock between find and
        # create. Re-read it and apply the same idempotency contract.
        if str(exc)!='idempotency_race':
            raise
        old=geometry_store.find_by_key(user,idempotency_key)
        if old is None:
            raise
        if old.request_hash!=request_hash:
            raise HTTPException(409,'idempotency_conflict')
        return old,False


@router.get('/config')
def config(user=Depends(identity)):
    settings=model_settings()
    return {'configured':settings.configured,**settings.public(),'api_key_configured':bool(settings.api_key.get_secret_value())}


@router.post('/repair')
def repair(body:RepairRequest,idempotency_key:Annotated[str,Header(min_length=1,max_length=200)],user=Depends(identity)):
    config=model_settings()
    run,created=open_run(body,user,idempotency_key,config)
    if not created:
        return response(run)
    journal=InMemoryGeometryJournal(geometry_store,run.id)
    verifier=DeterministicGeometryVerifier(load_config().model_copy(update={'maximum_iterations':body.max_iterations}))
    journal.emit('CONFIG',{'config':verifier.config.model_dump(mode='json'),'version':verifier.version,'policy':'llm-repair-v1','llm':config.public()})
    policy=LLMRepairPolicy(config,emit=journal.emit,maximum_move_m=verifier.config.maximum_move_m)
    try:
        result=repair_scene(body.scene,verifier,policy,journal.emit)
    except LLMRepairError as exc:
        journal.emit('RUN_FAILED',{'code':str(exc)})
        geometry_store.update(run.id,status='failed',error=str(exc))
        data=response(run)
        return JSONResponse(status_code=502,content=data)
    journal.emit('FINAL_RESULT',result.model_dump(mode='json'))
    geometry_store.update(run.id,status=result.status,result=result.model_dump(mode='json'))
    return response(run)


@router.post('/repair-graph')
def repair_graph(body:ReactRepairRequest,idempotency_key:Annotated[str,Header(min_length=1,max_length=200)],user=Depends(identity)):
    """Run the PDF-defined Critic -> LLM Tool Router -> Tool -> Verify loop.

    ``/geometry/repair`` remains the legacy MOVE contract for clients already
    using it.  This endpoint exposes the diagnosis-driven tool protocol without
    giving the model a free-form translation interface.
    """

    config=model_settings()
    storage_key='react:'+idempotency_key
    run,created=open_run(body,user,storage_key,config)
    if not created:
        return response(run)
    journal=InMemoryGeometryJournal(geometry_store,run.id)
    verifier=DeterministicGeometryVerifier(load_config().model_copy(update={'maximum_iterations':body.max_iterations}))
    journal.emit('CONFIG',{'config':verifier.config.model_dump(mode='json'),'version':verifier.version,'policy':'explicit-react-tool-routing-v1','tools':[tool['function']['name'] for tool in default_repair_tool_registry(verifier.config).schemas],'llm':config.public()})
    registry=default_repair_tool_registry(verifier.config)
    router_client=GeometryRepairRouter(config,emit=journal.emit,registry=registry)
    try:
        result=run_repair_loop(body.scene,verifier,router_client,registry=registry,max_iterations=body.max_iterations,emit=journal.emit)
    except LLMRepairError as exc:
        journal.emit('RUN_FAILED',{'code':str(exc)})
        geometry_store.update(run.id,status='failed',error=str(exc))
        data=response(run)
        return JSONResponse(status_code=502,content=data)
    geometry_store.update(run.id,status=result.status,result=result.model_dump(mode='json'))
    return response(run)


def owned(run_id,user):
    run=geometry_store.get_owned(run_id,user)
    if run is None:
        raise HTTPException(404,'geometry_run_not_found')
    return run


@router.get('/repairs/{run_id}')
def get_run(run_id:str,user=Depends(identity)):
    return response(owned(run_id,user))


@router.get('/repairs/{run_id}/events')
def events(run_id:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200),user=Depends(identity)):
    owned(run_id,user)
    rows,next_cursor=geometry_store.page_events(run_id,user,after,limit)
    return {'items':[{'sequence':r.sequence,'type':r.type,'payload':r.payload} for r in rows],
            'next_cursor':next_cursor}
