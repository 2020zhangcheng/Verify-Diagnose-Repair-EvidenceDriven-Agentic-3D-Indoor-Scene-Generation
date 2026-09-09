"""In-process run storage for the standalone Geometry Repair API.

Geometry Repair is a synchronous experiment.  It only needs a small
thread-safe run registry and an append-only event list while the process is
alive; the durable PostgreSQL event store belongs to the unrelated /tasks
workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from threading import RLock
from typing import Any
from uuid import uuid4


def digest(value: Any) -> str:
    """Return a stable request hash without importing the database layer."""

    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class GeometryMemoryEvent:
    sequence: int
    type: str
    payload: dict[str, Any]


@dataclass
class GeometryMemoryRun:
    id: str
    user_id: str
    idempotency_key: str
    request_hash: str
    status: str
    sequence: int = 1
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[GeometryMemoryEvent] = field(default_factory=list)


class InMemoryGeometryStore:
    """Process-local, thread-safe storage for Geometry Repair runs."""

    def __init__(self):
        self._lock = RLock()
        self._runs: dict[str, GeometryMemoryRun] = {}
        self._keys: dict[tuple[str, str], str] = {}

    def find_by_key(self, user_id: str, idempotency_key: str) -> GeometryMemoryRun | None:
        with self._lock:
            run_id = self._keys.get((user_id, idempotency_key))
            return self._runs.get(run_id) if run_id else None

    def create(
        self,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
        request_payload: dict[str, Any],
    ) -> GeometryMemoryRun:
        with self._lock:
            key = (user_id, idempotency_key)
            if key in self._keys:
                raise ValueError("idempotency_race")
            run = GeometryMemoryRun(
                id=str(uuid4()),
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="running",
                sequence=1,
                events=[GeometryMemoryEvent(sequence=1, type="USER_REQUEST", payload=request_payload)],
            )
            self._runs[run.id] = run
            self._keys[key] = run.id
            return run

    def get_owned(self, run_id: str, user_id: str) -> GeometryMemoryRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run if run is not None and run.user_id == user_id else None

    def emit(self, run_id: str, event_type: str, payload: dict[str, Any]) -> GeometryMemoryEvent:
        with self._lock:
            run = self._runs[run_id]
            run.sequence += 1
            event = GeometryMemoryEvent(sequence=run.sequence, type=event_type, payload=payload)
            run.events.append(event)
            return event

    def update(
        self,
        run_id: str,
        *,
        status: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> GeometryMemoryRun:
        with self._lock:
            run = self._runs[run_id]
            if status is not None:
                run.status = status
            if result is not None:
                run.result = result
            if error is not None:
                run.error = error
            return run

    def page_events(
        self,
        run_id: str,
        user_id: str,
        after: int,
        limit: int,
    ) -> tuple[list[GeometryMemoryEvent], int | None]:
        with self._lock:
            run = self.get_owned(run_id, user_id)
            if run is None:
                return [], None
            rows = [event for event in run.events if event.sequence > after]
            page = rows[: limit + 1]
            items = page[:limit]
            next_cursor = items[-1].sequence if len(page) > limit else None
            return items, next_cursor

    def clear(self) -> None:
        """Clear all process-local state; intended for isolated tests."""

        with self._lock:
            self._runs.clear()
            self._keys.clear()


class InMemoryGeometryJournal:
    """Adapter matching the event sink used by the repair loop."""

    def __init__(self, store: InMemoryGeometryStore, run_id: str):
        self.store = store
        self.run_id = run_id

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.store.emit(self.run_id, kind, payload)
