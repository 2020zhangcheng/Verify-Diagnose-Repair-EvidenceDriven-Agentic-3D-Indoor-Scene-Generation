"""Debounced DB fake sink. No LLM, embeddings or Mem0 calls in V0."""
from datetime import timedelta
import logging
import time
from sqlalchemy import select, or_, and_
from app.config import settings
from app.db.postgres import SessionLocal
from app.db.models.tables import MemoryJob, MemoryReceipt, EventRow, now
from app.db.repositories.store import uid


def process_one():
    with SessionLocal.begin() as session:
        job = session.scalar(select(MemoryJob).where(or_(
            and_(MemoryJob.status == "pending", MemoryJob.run_after <= now()),
            and_(MemoryJob.status == "processing", MemoryJob.lease_until < now())
        )).order_by(MemoryJob.run_after).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return False
        job.status = "processing"
        job.attempts += 1
        token = uid()
        job.lease_token = token
        job.lease_until = now() + timedelta(seconds=30)
        ident = job.id
    try:
        with SessionLocal.begin() as session:
            job = session.get(MemoryJob, ident, with_for_update=True)
            if job.lease_token != token or job.lease_until < now():
                return False
            batch = f"{job.task_id}:{job.from_seq}:{job.through_seq}"
            if session.get(MemoryReceipt, batch) is None:
                ids = list(session.scalars(select(EventRow.id).where(EventRow.task_id == job.task_id,
                    EventRow.sequence.between(job.from_seq, job.through_seq)).order_by(EventRow.sequence)))
                session.add(MemoryReceipt(batch_key=batch, event_ids=ids))
            job.status = "completed"
            job.lease_until = None
        return True
    except Exception as exc:
        with SessionLocal.begin() as session:
            job = session.get(MemoryJob, ident, with_for_update=True)
            if job.lease_token == token:
                job.status = "dead" if job.attempts >= 5 else "pending"
                job.last_error = type(exc).__name__
                job.run_after = now() + timedelta(seconds=2**job.attempts)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            process_one()
        except Exception:
            logging.exception("Fake memory batch failed")
        time.sleep(settings.poll_interval)
