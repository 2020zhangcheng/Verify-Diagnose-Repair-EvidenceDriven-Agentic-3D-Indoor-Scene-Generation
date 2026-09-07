from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from langgraph.checkpoint.postgres import PostgresSaver
from app.config import settings
from app.db.postgres import SessionLocal, engine
from app.db.models.tables import Task, EventRow, EntityRecord, MemoryJob, MemoryReceipt, now
from app.db.repositories.store import locked_task, append_event, lifecycle
from app.workers.agent_runner import run_task
from app.workers.memory_worker import process_one
from app.agent.graph import SimulatedCrash

pytestmark = pytest.mark.integration
AUTH = {"Authorization": "Bearer roomscout-local-demo"}


def headers(key=None):
    return {**AUTH, "Idempotency-Key": key or str(uuid4())}


def create(client, auto_run=True):
    response = client.post("/tasks", headers=headers(), json={"request": "把书桌移动到窗户附近", "auto_run": auto_run})
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


def event_rows(task_id):
    with SessionLocal() as session:
        return list(session.scalars(select(EventRow).where(EventRow.task_id == task_id).order_by(EventRow.sequence)))


def test_task_creation_and_event_first(client):
    ident = create(client, False)
    rows = event_rows(ident)
    assert [r.type for r in rows] == ["TASK_CREATED", "USER_MESSAGE"]
    assert [r.sequence for r in rows] == [1, 2]
    with SessionLocal() as session:
        task = session.get(Task, ident)
        assert task.status == "created"
        job = session.scalar(select(MemoryJob).where(MemoryJob.task_id == ident))
        assert (job.from_seq, job.through_seq) == (1, 2)


def test_idempotency_and_conflict(client):
    h = headers()
    body = {"request": "test", "auto_run": False}
    first = client.post("/tasks", headers=h, json=body)
    assert client.post("/tasks", headers=h, json=body).json() == first.json()
    assert client.post("/tasks", headers=h, json={**body, "request": "changed"}).status_code == 409
    assert len(event_rows(first.json()["task_id"])) == 2


def test_auth_and_project_scope(client):
    assert client.post("/tasks", headers={"Idempotency-Key": "x"}, json={"request": "x"}).status_code == 401
    assert client.post("/tasks", headers=headers(), json={"request": "x", "project_id": "other"}).status_code == 404
    assert client.post("/tasks", headers=AUTH, json={"request": "x"}).status_code == 422


@pytest.mark.parametrize("statement", ["UPDATE events SET type='bad' WHERE task_id=:id", "DELETE FROM events WHERE task_id=:id", "TRUNCATE events CASCADE"])
def test_append_only_database_guard(client, statement):
    ident = create(client, False)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement), {"id": ident})
    assert len(event_rows(ident)) == 2


def test_event_projection_rollback(client):
    ident = create(client, False)
    with pytest.raises(RuntimeError):
        with SessionLocal.begin() as session:
            task = locked_task(session, ident)
            append_event(session, task, "RUN_REQUESTED", lifecycle("RUN_REQUESTED", ("run",)), "rollback")
            task.status = "queued"
            raise RuntimeError("crash before commit")
    assert len(event_rows(ident)) == 2
    with SessionLocal() as session:
        assert session.get(Task, ident).status == "created"


def test_graph_normal_and_conditional_loop(client):
    ident = create(client)
    result = run_task(ident)
    assert result["stage"] == "finished"
    assert result["observation_round"] == 2
    assert result["budget"]["remaining_views"] == 0
    assert len(result["observation_ids"]) == 3  # initial, active view, post-action validation
    rows = event_rows(ident)
    kinds = [r.type for r in rows]
    expected = ["TASK_CREATED", "OBSERVATION_CREATED", "BELIEF_UPDATED", "LAYOUT_GENERATED", "UNKNOWN_DETECTED",
                "VIEW_SELECTED", "NEW_OBSERVATION", "BELIEF_UPDATED", "VERIFICATION", "ACTION_EXECUTED", "TASK_FINISHED"]
    cursor = 0
    for kind in expected:
        cursor = kinds.index(kind, cursor) + 1
    assert kinds.count("BELIEF_UPDATED") == 2
    assert kinds.count("VIEW_SELECTED") == 1
    assert kinds.count("LAYOUT_GENERATED") == 6
    assert kinds.count("ACTION_EXECUTED") == 1
    assert [r.sequence for r in rows] == list(range(1, len(rows)+1))
    # Every result follows a committed call, and causation tracks the actual event chain.
    for previous, current in zip(rows, rows[1:]):
        assert current.envelope["causation_id"] == previous.id
    status = client.get(f"/tasks/{ident}", headers=AUTH).json()
    assert status["status"] == "finished" and status["final_result"]["validated"]
    assert client.get(f"/tasks/{ident}/belief", headers=AUTH).json()["belief"]["ref"]["version"] == 2


def test_checkpoint_resume_new_graph_instance(client):
    ident = create(client)
    first = run_task(ident, interrupt_before=["verify_layout"])
    assert first["stage"] == "find_unknowns"
    with SessionLocal() as session:
        run_id = session.get(Task, ident).run_id
    with PostgresSaver.from_conn_string(settings.checkpoint_url) as saver:
        saved = saver.get_tuple({"configurable": {"thread_id": run_id}})
        assert saved is not None
    count = len(event_rows(ident))
    result = run_task(ident)
    assert result["stage"] == "finished"
    assert len(event_rows(ident)) > count
    assert [r.type for r in event_rows(ident)].count("OBSERVATION_CREATED") == 3


@pytest.mark.parametrize("node", ["observe_scene", "select_best_view", "execute", "validate"])
def test_crash_after_event_before_checkpoint(client, node):
    ident = create(client)
    with pytest.raises(SimulatedCrash):
        run_task(ident, crash_after=node)
    before = [r.id for r in event_rows(ident)]
    result = run_task(ident)
    assert result["stage"] == "finished"
    after = event_rows(ident)
    assert [r.id for r in after[:len(before)]] == before
    kinds = [r.type for r in after]
    assert kinds.count("ACTION_EXECUTED") == 1
    assert kinds.count("VIEW_SELECTED") == 1
    assert kinds.count("OBSERVATION_CREATED") == 3


def test_concurrent_runner_single_writer(client):
    ident = create(client)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run_task, [ident, ident]))
    assert [r.type for r in event_rows(ident)].count("ACTION_EXECUTED") == 1


def test_explicit_run_and_events_paging(client):
    ident = create(client, False)
    h = headers()
    first = client.post(f"/tasks/{ident}/run", headers=h, json={"resume": True})
    assert first.status_code == 202
    assert client.post(f"/tasks/{ident}/run", headers=h, json={"resume": True}).json() == first.json()
    run_task(ident)
    seen = []
    after = 0
    while True:
        page = client.get(f"/tasks/{ident}/events?after={after}&limit=7", headers=AUTH).json()
        seen.extend(x["event_id"] for x in page["items"])
        if page["next_cursor"] is None:
            break
        after = page["next_cursor"]
    assert seen == [r.id for r in event_rows(ident)]
    assert client.post(f"/tasks/{ident}/run", headers=headers(), json={}).status_code == 409


def test_fake_memory_job_lease_recovery(client):
    ident = create(client, False)
    with SessionLocal.begin() as session:
        job = session.scalar(select(MemoryJob).where(MemoryJob.task_id == ident))
        job.status = "processing"
        job.lease_until = now() - timedelta(seconds=1)
        job.run_after = now() - timedelta(days=1)
    assert process_one()
    with SessionLocal() as session:
        job = session.scalar(select(MemoryJob).where(MemoryJob.task_id == ident))
        assert job.status == "completed"
        receipt = session.get(MemoryReceipt, f"{ident}:1:2")
        assert len(receipt.event_ids) == 2


def test_process_death_releases_lock_and_recovers(client):
    import subprocess
    import sys
    ident = create(client)
    script = "from app.workers.agent_runner import run_task; run_task(" + repr(ident) + ", crash_after='hard:execute')"
    killed = subprocess.run([sys.executable, "-c", script], timeout=30)
    assert killed.returncode == 86
    assert [r.type for r in event_rows(ident)].count("ACTION_EXECUTED") == 1
    result = run_task(ident)
    assert result["stage"] == "finished"
    assert [r.type for r in event_rows(ident)].count("ACTION_EXECUTED") == 1


def test_trace_resolves_evidence_chain(client):
    ident = create(client)
    run_task(ident)
    response = client.get(f"/tasks/{ident}/trace", headers=AUTH)
    assert response.status_code == 200, response.text
    trace = response.json()
    ids = {n["id"] for n in trace["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in trace["edges"])
    kinds = {n["kind"] for n in trace["nodes"]}
    assert {"action", "decision", "layout", "assumption", "claim", "belief", "observation", "camera_view", "verification"} <= kinds
    assert len(client.get(f"/tasks/{ident}/layouts", headers=AUTH).json()["items"]) == 3


def test_event_type_must_match_payload(client):
    ident = create(client, False)
    with pytest.raises(ValueError, match="type/payload"):
        with SessionLocal.begin() as session:
            task = locked_task(session, ident)
            append_event(session, task, "ACTION_EXECUTED", lifecycle("TASK_FINISHED"), "bad-type")
    assert len(event_rows(ident)) == 2


def test_new_events_do_not_extend_retrying_memory_batch(client):
    ident = create(client, False)
    with SessionLocal.begin() as session:
        job = session.scalar(select(MemoryJob).where(MemoryJob.task_id == ident))
        job.attempts = 1  # A sealed batch pending retry.
        task = locked_task(session, ident)
        append_event(session, task, "RUN_REQUESTED", lifecycle("RUN_REQUESTED", ("r",)), "new-job")
    with SessionLocal() as session:
        jobs = list(session.scalars(select(MemoryJob).where(MemoryJob.task_id == ident).order_by(MemoryJob.from_seq)))
        assert [(j.from_seq, j.through_seq) for j in jobs] == [(1, 2), (3, 3)]
