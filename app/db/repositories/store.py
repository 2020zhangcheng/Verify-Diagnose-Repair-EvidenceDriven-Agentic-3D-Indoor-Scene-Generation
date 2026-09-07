"""Transactions own the event sequence, projections and durable memory outbox."""
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4
from sqlalchemy import select
from app.config import settings
from app.contracts.events import Event, Payload
from pydantic import TypeAdapter
from app.db.models.tables import EventRow, MemoryJob, Project, Task, EntityRecord, now


def uid():
    return str(uuid4())


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def append_event(session, task, event_type, payload, key, producer="roomscout-v0"):
    parsed = TypeAdapter(Payload).validate_python(payload)
    if parsed.kind == "ENTITY_RECORDED":
        entity = parsed.entity
        mapping = {"Observation": "OBSERVATION_CREATED", "SceneBelief": "BELIEF_UPDATED",
                   "CandidateLayout": "LAYOUT_GENERATED", "CriticalUnknown": "UNKNOWN_DETECTED",
                   "Decision": "DECISION_RECORDED", "TaskSpec": "TASK_UNDERSTOOD"}
        kind = type(entity).__name__
        if kind == "CandidateView":
            allowed = {"VIEW_SELECTED", "VIEW_CANDIDATE_GENERATED"}
        elif kind == "VerificationResult":
            allowed = {"VERIFICATION_" + {"pass": "PASSED", "fail": "FAILED", "unknown": "UNKNOWN", "error": "ERROR"}[entity.status]}
        elif kind == "ActionResult":
            allowed = {"ACTION_" + {"succeeded": "EXECUTED", "failed": "FAILED", "partial": "PARTIAL", "unknown": "UNKNOWN"}[entity.status]}
        else:
            allowed = {mapping[kind]}
        if hasattr(entity, "task_id") and entity.task_id != task.id:
            raise ValueError("cross-task entity")
    else:
        allowed = {parsed.kind}
    if event_type not in allowed:
        raise ValueError("event type/payload mismatch")
    payload = parsed.model_dump(mode="json")
    existing = session.scalar(select(EventRow).where(EventRow.task_id == task.id, EventRow.idempotency_key == key))
    if existing:
        if existing.type != event_type or TypeAdapter(Payload).validate_python(existing.payload).model_dump(mode="json") != payload:
            raise ValueError("event idempotency conflict")
        return existing
    # Caller locks task row for every write transaction.
    task.sequence += 1
    project = session.get(Project, task.project_id)
    previous = session.scalar(select(EventRow.id).where(EventRow.task_id == task.id).order_by(EventRow.sequence.desc()).limit(1))
    timestamp = now()
    envelope = Event(event_id=uid(), task_id=task.id, project_id=project.id,
                     user_id=project.user_id, run_id=task.run_id, sequence=task.sequence,
                     idempotency_key=key, causation_id=previous, correlation_id=task.id,
                     occurred_at=timestamp, recorded_at=timestamp, producer=producer,
                     payload=payload).model_dump(mode="json")
    row = EventRow(id=envelope["event_id"], task_id=task.id, sequence=task.sequence,
                   type=event_type, idempotency_key=key, payload=envelope["payload"], envelope=envelope)
    session.add(row)
    session.flush()
    job = session.scalar(select(MemoryJob).where(MemoryJob.task_id == task.id, MemoryJob.status == "pending", MemoryJob.attempts == 0).with_for_update())
    if job is None:
        job = MemoryJob(id=uid(), task_id=task.id, scope_key=f"{project.user_id}/{project.id}/roomscout",
                        from_seq=task.sequence, through_seq=task.sequence,
                        run_after=timestamp + timedelta(seconds=settings.memory_debounce_seconds))
        session.add(job)
    else:
        job.through_seq = task.sequence
        job.run_after = timestamp + timedelta(seconds=settings.memory_debounce_seconds)
    return row


def lifecycle(kind, ids=(), reason=None):
    return {"schema_version": 1, "kind": kind, "entity_ids": list(ids), "reason": reason}


def locked_task(session, task_id):
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise LookupError(task_id)
    return task


def entity_id(entity):
    data = entity.model_dump(mode="json")
    if "ref" in data:
        return f"{data['ref']['belief_id']}@{data['ref']['version']}"
    names = {"Observation": "observation_id", "CandidateLayout": "layout_id",
             "CriticalUnknown": "unknown_id", "CandidateView": "view_id",
             "VerificationResult": "verification_id", "ActionResult": "action_id", "Decision": "decision_id"}
    if type(entity).__name__ == "TaskSpec":
        return f"{data['task_id']}:spec"
    return data[names[type(entity).__name__]]



def save_entity(session, task, entity, event_type, key):
    data = entity.model_dump(mode="json")
    event = append_event(session, task, event_type, {"kind": "ENTITY_RECORDED", "entity": data}, key)
    existing = session.get(EntityRecord, (task.id, entity_id(entity)))
    if existing:
        if type(entity).model_validate(existing.payload).model_dump(mode="json") != data:
            raise ValueError("immutable entity conflict")
        return existing
    record = EntityRecord(task_id=task.id, entity_id=entity_id(entity), kind=type(entity).__name__, payload=data, source_event_id=event.id)
    session.add(record)
    session.flush()
    return record


def load_entity(session, task_id, ident, model):
    row = session.get(EntityRecord, (task_id, ident))
    if row is None or row.kind != model.__name__:
        raise LookupError(f"Missing {model.__name__}: {ident}")
    return model.model_validate(row.payload)
