from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from app.config import settings
from app.db.postgres import SessionLocal
from app.db.models.tables import Task, Project, EventRow, APIReceipt, EntityRecord
from app.db.repositories.store import uid, digest, append_event, lifecycle, locked_task

router = APIRouter()


def identity(authorization: Annotated[str | None, Header()] = None):
    import secrets
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {settings.demo_token}"):
        raise HTTPException(401, "unauthorized")
    return "demo-user"


def get_task(session, ident, user):
    task = session.scalar(select(Task).join(Project).where(Task.id == ident, Project.user_id == user))
    if task is None:
        raise HTTPException(404, "task_not_found")
    return task


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    project_id: str = "demo-project"
    request: str = Field(min_length=1, max_length=10000)
    config_id: Literal["fake-v0", "scene-belief-v1"] = "fake-v0"
    seed: int = Field(default=0, ge=0, le=2147483647)
    auto_run: bool = True


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    resume: Literal[True] = True


def receipt(session, scope, body):
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 1))"), {"scope": scope})
    old = session.get(APIReceipt, scope)
    if old and old.request_hash != digest(body):
        raise HTTPException(409, "idempotency_conflict")
    return old.response if old else None


@router.post("/tasks", status_code=201)
def create_task(body: CreateTask, idempotency_key: Annotated[str, Header(min_length=1, max_length=200)], user=Depends(identity)):
    with SessionLocal.begin() as session:
        project = session.get(Project, body.project_id)
        if project is None or project.user_id != user:
            raise HTTPException(404, "project_not_found")
        scope = f"{user}:POST:/tasks:{idempotency_key}"
        old = receipt(session, scope, body.model_dump())
        if old:
            return old
        task = Task(id=uid(), project_id=project.id, request=body.request, config_id=body.config_id,
                    seed=body.seed, sequence=0, status="created", stage="created")
        session.add(task)
        session.flush()
        event = append_event(session, task, "TASK_CREATED", lifecycle("TASK_CREATED", (task.id,)), "created")
        append_event(session, task, "USER_MESSAGE", {"kind": "USER_MESSAGE", "message": body.request}, "user-message")
        if body.auto_run:
            task.run_id = uid()
            append_event(session, task, "RUN_REQUESTED", lifecycle("RUN_REQUESTED", (task.run_id,)), "auto-run")
            task.status = "queued"
        response = dict(schema_version=1, task_id=task.id, status="created", stage="created", event_id=event.id)
        session.add(APIReceipt(scope=scope, request_hash=digest(body.model_dump()), response=response))
        return response


@router.post("/tasks/{task_id}/run", status_code=202)
def start_run(task_id: str, body: RunRequest, idempotency_key: Annotated[str, Header(min_length=1, max_length=200)], user=Depends(identity)):
    with SessionLocal.begin() as session:
        get_task(session, task_id, user)
        scope = f"{user}:POST:/tasks/{task_id}/run:{idempotency_key}"
        old = receipt(session, scope, body.model_dump())
        if old:
            return old
        task = locked_task(session, task_id)
        if task.status in ("finished", "blocked"):
            raise HTTPException(409, "task_not_resumable")
        if task.status not in ("queued", "running"):
            task.run_id = task.run_id or uid()
            event = append_event(session, task, "RUN_REQUESTED", lifecycle("RUN_REQUESTED", (task.run_id,)), f"run:{idempotency_key}")
            task.status = "queued"
            event_id = event.id
        else:
            event_id = session.scalar(select(EventRow.id).where(EventRow.task_id == task.id, EventRow.type == "RUN_REQUESTED").order_by(EventRow.sequence.desc()).limit(1))
        response = dict(schema_version=1, task_id=task.id, run_id=task.run_id, status="queued", event_id=event_id)
        session.add(APIReceipt(scope=scope, request_hash=digest(body.model_dump()), response=response))
        return response


@router.get("/tasks/{task_id}")
def status(task_id: str, user=Depends(identity)):
    with SessionLocal() as session:
        task = get_task(session, task_id, user)
        return dict(schema_version=1, task_id=task.id, status=task.status, stage=task.stage, run_id=task.run_id,
                    belief_ref=task.state.get("scene_belief"), last_event_sequence=task.sequence,
                    blocked_reason=task.error or (task.state.get("final_result") or {}).get("reason"), final_result=task.state.get("final_result"))


@router.get("/tasks/{task_id}/events")
def events(task_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=200), user=Depends(identity)):
    with SessionLocal() as session:
        get_task(session, task_id, user)
        rows = list(session.scalars(select(EventRow).where(EventRow.task_id == task_id, EventRow.sequence > after).order_by(EventRow.sequence).limit(limit+1)))
        return dict(schema_version=1, items=[{"type": r.type, **r.envelope} for r in rows[:limit]],
                    next_cursor=rows[limit-1].sequence if len(rows) > limit else None)


@router.get("/tasks/{task_id}/belief")
def belief(task_id: str, version: int | None = Query(None, ge=1), user=Depends(identity)):
    with SessionLocal() as session:
        task = get_task(session, task_id, user)
        ref = task.state.get("scene_belief")
        if not ref:
            if version:
                raise HTTPException(404, "belief_not_found")
            return {"schema_version": 1, "task_id": task_id, "belief": None}
        row = session.get(EntityRecord, (task_id, f"{ref['belief_id']}@{version or ref['version']}"))
        if row is None:
            raise HTTPException(404, "belief_not_found")
        return {"schema_version": 1, "task_id": task_id, "belief": row.payload}


def entity_page(task_id, kind, user, belief_version, cursor, limit, status_filter=None):
    import base64
    import json
    with SessionLocal() as session:
        task = get_task(session, task_id, user)
        current = task.state.get("scene_belief")
        version = belief_version or (current or {}).get("version")
        offset = 0
        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if data["task"] != task_id or data["kind"] != kind or data.get("status") != status_filter or (belief_version and belief_version != data["version"]):
                    raise ValueError()
                version, offset = data["version"], data["offset"]
                if not isinstance(offset, int) or offset < 0:
                    raise ValueError()
            except Exception:
                raise HTTPException(400, "invalid_cursor")
        rows = list(session.scalars(select(EntityRecord).where(EntityRecord.task_id == task_id, EntityRecord.kind == kind).order_by(EntityRecord.entity_id)))
        items = [r.payload for r in rows if r.payload.get("belief_ref", {}).get("version") == version and (status_filter is None or r.payload.get("status") == status_filter)]
        next_cursor = None
        if offset+limit < len(items):
            next_cursor = base64.urlsafe_b64encode(json.dumps({"task": task_id, "kind": kind, "version": version, "offset": offset+limit, "status": status_filter}).encode()).decode()
        return {"schema_version": 1, "items": items[offset:offset+limit], "next_cursor": next_cursor,
                "belief_ref": {**current, "version": version} if current else None}


@router.get("/tasks/{task_id}/layouts")
def layouts(task_id: str, belief_version: int | None = Query(None, ge=1), cursor: str | None = None,
            limit: int = Query(50, ge=1, le=200), user=Depends(identity)):
    return entity_page(task_id, "CandidateLayout", user, belief_version, cursor, limit)


@router.get("/tasks/{task_id}/unknowns")
def unknowns(task_id: str, status: Literal["open", "resolved", "superseded"] | None = None, belief_version: int | None = Query(None, ge=1), cursor: str | None = None,
             limit: int = Query(50, ge=1, le=200), user=Depends(identity)):
    return entity_page(task_id, "CriticalUnknown", user, belief_version, cursor, limit, status)


@router.get("/tasks/{task_id}/views")
def views(task_id: str, kind: Literal["candidate", "executed"] = "candidate", belief_version: int | None = Query(None, ge=1), cursor: str | None = None,
          limit: int = Query(50, ge=1, le=200), user=Depends(identity)):
    if kind == "executed":
        if cursor or belief_version:
            raise HTTPException(400, "executed_views_use_event_cursor")
        with SessionLocal() as session:
            get_task(session, task_id, user)
            rows = list(session.scalars(select(EntityRecord).where(EntityRecord.task_id == task_id, EntityRecord.kind == "Observation").order_by(EntityRecord.entity_id)))
            items = [{"camera_view_id": r.payload["camera_view_id"], "candidate_view_id": r.payload["camera_view_id"].removesuffix(":executed") if r.payload["camera_view_id"].endswith(":executed") else None,
                      "camera_pose": r.payload["camera_pose"], "environment_revision": r.payload["environment_revision"],
                      "observation_ids": [r.entity_id], "actual_cost_m": 1 if r.payload["camera_view_id"].endswith(":executed") else 0} for r in rows]
            return {"schema_version": 1, "items": items, "next_cursor": None, "belief_ref": None}
    return entity_page(task_id, "CandidateView", user, belief_version, cursor, limit)


@router.get("/tasks/{task_id}/trace")
def trace(task_id: str, user=Depends(identity)):
    from app.api.trace import build_trace
    with SessionLocal() as session:
        get_task(session, task_id, user)
        rows = list(session.scalars(select(EntityRecord).where(EntityRecord.task_id == task_id).order_by(EntityRecord.entity_id)))
        return {"schema_version": 1, "task_id": task_id, **build_trace(rows), "next_cursor": None}
