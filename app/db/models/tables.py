from datetime import datetime, timezone
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(Text)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    request: Mapped[str] = mapped_column(Text)
    config_id: Mapped[str] = mapped_column(Text, default="fake-v0")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="created")
    sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    run_id: Mapped[str | None] = mapped_column(Text)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("task_id", "sequence"), UniqueConstraint("task_id", "idempotency_key"))
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    envelope: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryJob(Base):
    __tablename__ = "memory_jobs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    scope_key: Mapped[str] = mapped_column(Text)
    from_seq: Mapped[int] = mapped_column(BigInteger)
    through_seq: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, default="pending")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_memory_due", "status", "run_after"),)


class MemoryReceipt(Base):
    __tablename__ = "memory_receipts"
    batch_key: Mapped[str] = mapped_column(Text, primary_key=True)
    event_ids: Mapped[list] = mapped_column(JSONB)
    sink_version: Mapped[str] = mapped_column(Text, default="fake-v0")


class EntityRecord(Base):
    __tablename__ = "entity_records"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    source_event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    node: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="started")
    patch: Mapped[dict | None] = mapped_column(JSONB)


class APIReceipt(Base):
    __tablename__ = "api_receipts"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSONB)
