from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import text
from langgraph.checkpoint.postgres import PostgresSaver
from app.config import settings
from app.db.postgres import SessionLocal
from app.db.models.tables import User, Project


def bootstrap():
    with SessionLocal.begin() as session:
        session.execute(insert(User).values(id="demo-user").on_conflict_do_nothing())
        session.execute(insert(Project).values(id="demo-project", user_id="demo-user", name="RoomScout Fake V0").on_conflict_do_nothing())
    with PostgresSaver.from_conn_string(settings.checkpoint_url) as saver:
        saver.setup()
    with SessionLocal.begin() as session:
        session.execute(text("""DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='roomscout_app') THEN
            CREATE ROLE roomscout_app LOGIN PASSWORD 'roomscout_local_app';
          END IF;
        END $$"""))
        session.execute(text("GRANT USAGE ON SCHEMA public, checkpoints TO roomscout_app"))
        session.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO roomscout_app"))
        session.execute(text("REVOKE UPDATE, DELETE ON events, geometry_run_events FROM roomscout_app"))
        session.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA checkpoints TO roomscout_app"))
        session.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public, checkpoints TO roomscout_app"))


if __name__ == "__main__":
    bootstrap()
