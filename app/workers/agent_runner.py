"""Durable DB queue; process death releases session locks and next worker resumes."""
import logging
import time
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from langgraph.checkpoint.postgres import PostgresSaver
from app.config import settings
from app.db.postgres import engine, SessionLocal
from app.db.models.tables import Task
from app.db.repositories.store import locked_task, append_event, lifecycle
from app.agent.graph import build_graph, initial_state, SimulatedCrash
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)


def run_task(task_id, *, interrupt_before=None, crash_after=None):
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:id, 0))"), {"id": task_id})
        connection.commit()
        if not acquired:
            return None
        try:
            with Session(connection) as session, session.begin():
                task = locked_task(session, task_id)
                if task.status not in ("queued", "running"):
                    return task.state
                task.status = "running"
                state = initial_state(task)
                config = {"configurable": {"thread_id": task.run_id}, "recursion_limit": 50}
            with PostgresSaver.from_conn_string(settings.checkpoint_url) as saver:
                graph = build_graph(saver, connection, interrupt_before=interrupt_before, crash_after=crash_after)
                snapshot = graph.get_state(config)
                result = graph.invoke(None if snapshot.values else state, config, durability="sync")
                snapshot = graph.get_state(config)
                if not snapshot.next:
                    with Session(connection) as session, session.begin():
                        task = locked_task(session, task_id)
                        task.status = "finished" if result["stage"] == "finished" else "blocked"
                        task.error = None
                return result
        except Exception as exc:
            # Record terminal errors while still holding the same session lock.
            # Infrastructure failures stay recoverable in the durable queue.
            if not isinstance(exc, (SimulatedCrash, SQLAlchemyError)) and not connection.invalidated:
                with Session(connection) as session, session.begin():
                    task = locked_task(session, task_id)
                    append_event(session, task, "TASK_FAILED", lifecycle("TASK_FAILED", (task_id,), type(exc).__name__), f"failure:{task.sequence}")
                    task.status = "failed"
                    task.error = type(exc).__name__
            raise
        finally:
            if not connection.invalidated:
                connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))"), {"id": task_id})
                connection.commit()


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            with SessionLocal() as session:
                ids = list(session.scalars(select(Task.id).where(Task.status.in_(("queued", "running"))).order_by(Task.created_at).limit(20)))
            for task_id in ids:
                try:
                    run_task(task_id)
                except Exception as exc:
                    log.exception("Task failed: %s", task_id)
            time.sleep(settings.poll_interval)
        except Exception:
            log.exception("Worker polling failed; durable tasks retained")
            time.sleep(2)


if __name__ == "__main__":
    main()
