"""HTTP API for the Geometry Critic and explicit ReAct repair loop."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from app.api.auth import identity
from app.contracts.models import Contract
from app.repair.loop import run_repair_loop
from app.repair.registry import default_repair_tool_registry
from app.repair.router import GeometryRepairRouter
from app.verification.geometry import DeterministicGeometryVerifier, load_config
from app.verification.io import parse_snapshot
from app.verification.llm import LLMRepairError, LLMSettings
from app.verification.memory_persistence import InMemoryGeometryJournal, InMemoryGeometryStore, digest
from app.verification.models import SceneSnapshot


router = APIRouter(prefix="/geometry", tags=["Geometry Repair"])
geometry_store = InMemoryGeometryStore()


class RepairRequest(Contract):
    scene: SceneSnapshot
    max_iterations: int = Field(default=10, ge=1, le=10)

    @field_validator("scene", mode="before")
    @classmethod
    def scene_input(cls, value):
        if isinstance(value, SceneSnapshot):
            return value
        try:
            return parse_snapshot(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Scene requires objects with object_id, position_m and size_m") from exc

    @field_validator("scene")
    @classmethod
    def scene_limit(cls, value):
        if len(value.objects) > 100:
            raise ValueError("maximum 100 objects per synchronous repair")
        return value


def model_settings() -> LLMSettings:
    try:
        return LLMSettings()
    except ValueError:
        raise HTTPException(503, "llm_configuration_invalid") from None


def response(run):
    return {
        "run_id": run.id,
        "status": run.status,
        "result": run.result,
        "error": run.error,
        "events_url": f"/geometry/repairs/{run.id}/events",
    }


def open_run(body: RepairRequest, user: str, idempotency_key: str, config: LLMSettings):
    """Create or retrieve a process-local run with idempotency protection."""

    request_payload = body.model_dump(mode="json")
    request_hash = digest(request_payload)
    existing = geometry_store.find_by_key(user, idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "idempotency_conflict")
        return existing, False
    if not config.configured:
        raise HTTPException(503, "llm_not_configured")
    try:
        return geometry_store.create(user, idempotency_key, request_hash, request_payload), True
    except ValueError as exc:
        if str(exc) != "idempotency_race":
            raise
        existing = geometry_store.find_by_key(user, idempotency_key)
        if existing is None:
            raise
        if existing.request_hash != request_hash:
            raise HTTPException(409, "idempotency_conflict")
        return existing, False


def _run_repair(body: RepairRequest, run, config: LLMSettings):
    journal = InMemoryGeometryJournal(geometry_store, run.id)
    verifier = DeterministicGeometryVerifier(
        load_config().model_copy(update={"maximum_iterations": body.max_iterations})
    )
    registry = default_repair_tool_registry(verifier.config)
    journal.emit(
        "CONFIG",
        {
            "config": verifier.config.model_dump(mode="json"),
            "version": verifier.version,
            "policy": "explicit-react-tool-routing-v1",
            "tools": [tool["function"]["name"] for tool in registry.schemas],
            "llm": config.public(),
        },
    )
    router_client = GeometryRepairRouter(config, emit=journal.emit, registry=registry)
    return run_repair_loop(
        body.scene,
        verifier,
        router_client,
        registry=registry,
        max_iterations=body.max_iterations,
        emit=journal.emit,
    )


@router.get("/config")
def get_config(user=Depends(identity)):
    config = model_settings()
    return {
        "configured": config.configured,
        **config.public(),
        "api_key_configured": bool(config.api_key.get_secret_value()),
    }


@router.post("/repair")
def repair(
    body: RepairRequest,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    user=Depends(identity),
):
    """Run Critic → LLM tool selection → deterministic tool → verification."""

    config = model_settings()
    run, created = open_run(body, user, idempotency_key, config)
    if not created:
        return response(run)
    try:
        result = _run_repair(body, run, config)
    except LLMRepairError as exc:
        journal = InMemoryGeometryJournal(geometry_store, run.id)
        journal.emit("RUN_FAILED", {"code": str(exc)})
        geometry_store.update(run.id, status="failed", error=str(exc))
        return JSONResponse(status_code=502, content=response(run))
    result_payload = result.model_dump(mode="json")
    geometry_store.update(run.id, status=result.status, result=result_payload)
    return response(run)


def owned(run_id: str, user: str):
    run = geometry_store.get_owned(run_id, user)
    if run is None:
        raise HTTPException(404, "geometry_run_not_found")
    return run


@router.get("/repairs/{run_id}")
def get_run(run_id: str, user=Depends(identity)):
    return response(owned(run_id, user))


@router.get("/repairs/{run_id}/events")
def events(
    run_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    user=Depends(identity),
):
    owned(run_id, user)
    rows, next_cursor = geometry_store.page_events(run_id, user, after, limit)
    return {
        "items": [
            {"sequence": event.sequence, "type": event.type, "payload": event.payload}
            for event in rows
        ],
        "next_cursor": next_cursor,
    }
