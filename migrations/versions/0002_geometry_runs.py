"""Independent durable LLM geometry experiments."""
from alembic import op
revision='0002'
down_revision='0001'


def upgrade():
    op.execute('''CREATE TABLE geometry_runs (
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
      idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
      status TEXT NOT NULL, sequence BIGINT NOT NULL DEFAULT 0,
      result JSONB, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(user_id,idempotency_key))''')
    op.execute('''CREATE TABLE geometry_run_events (
      run_id TEXT NOT NULL REFERENCES geometry_runs(id), sequence BIGINT NOT NULL,
      type TEXT NOT NULL, payload JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(run_id,sequence))''')
    op.execute('CREATE TRIGGER geometry_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON geometry_run_events FOR EACH STATEMENT EXECUTE FUNCTION reject_event_mutation()')


def downgrade():
    op.drop_table('geometry_run_events')
    op.drop_table('geometry_runs')
