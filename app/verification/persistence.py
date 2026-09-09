"""Legacy PostgreSQL Event First journal.

The standalone Geometry API now uses ``memory_persistence``.  This module is
kept for the historical database migration and PostgreSQL integration
fixtures; it is not imported by the Geometry Repair endpoints.
"""
from datetime import datetime
from sqlalchemy import Text,BigInteger,ForeignKey,select,UniqueConstraint,DateTime,text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from app.db.models.tables import Base
from app.db.postgres import SessionLocal


class GeometryRun(Base):
    __tablename__='geometry_runs'
    __table_args__=(UniqueConstraint('user_id','idempotency_key'),)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=text('now()'))
    id: Mapped[str]=mapped_column(Text,primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'))
    idempotency_key: Mapped[str]=mapped_column(Text)
    request_hash: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(Text)
    sequence: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    result: Mapped[dict|None]=mapped_column(JSONB)
    error: Mapped[str|None]=mapped_column(Text)


class GeometryRunEvent(Base):
    __tablename__='geometry_run_events'
    recorded_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=text('now()'))
    run_id: Mapped[str]=mapped_column(ForeignKey('geometry_runs.id'),primary_key=True)
    sequence: Mapped[int]=mapped_column(BigInteger,primary_key=True)
    type: Mapped[str]=mapped_column(Text)
    payload: Mapped[dict]=mapped_column(JSONB)


class DatabaseJournal:
    def __init__(self,run_id):
        self.run_id=run_id

    def emit(self,kind,payload):
        with SessionLocal.begin() as session:
            run=session.scalar(select(GeometryRun).where(GeometryRun.id==self.run_id).with_for_update())
            run.sequence+=1
            session.add(GeometryRunEvent(run_id=run.id,sequence=run.sequence,type=kind,payload=payload))
