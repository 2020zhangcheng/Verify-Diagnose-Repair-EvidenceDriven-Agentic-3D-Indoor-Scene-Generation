"""V0 durable infrastructure; domain projections remain typed JSONB."""
from alembic import op
revision = "0001"
down_revision = None


def upgrade():
    op.execute('\nCREATE TABLE api_receipts (\n\tscope TEXT NOT NULL, \n\trequest_hash TEXT NOT NULL, \n\tresponse JSONB NOT NULL, \n\tPRIMARY KEY (scope)\n)\n\n')
    op.execute('\nCREATE TABLE memory_receipts (\n\tbatch_key TEXT NOT NULL, \n\tevent_ids JSONB NOT NULL, \n\tsink_version TEXT NOT NULL, \n\tPRIMARY KEY (batch_key)\n)\n\n')
    op.execute('\nCREATE TABLE users (\n\tid TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n')
    op.execute('\nCREATE TABLE projects (\n\tid TEXT NOT NULL, \n\tuser_id TEXT NOT NULL, \n\tname TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_projects_user_id ON projects (user_id)')
    op.execute('\nCREATE TABLE tasks (\n\tid TEXT NOT NULL, \n\tproject_id TEXT NOT NULL, \n\trequest TEXT NOT NULL, \n\tconfig_id TEXT NOT NULL, \n\tseed INTEGER NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tstage VARCHAR(64) NOT NULL, \n\tsequence BIGINT NOT NULL, \n\trun_id TEXT, \n\tstate JSONB NOT NULL, \n\terror TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id)\n)\n\n')
    op.execute('CREATE INDEX ix_tasks_project_id ON tasks (project_id)')
    op.execute('CREATE INDEX ix_tasks_status ON tasks (status)')
    op.execute('\nCREATE TABLE events (\n\tid TEXT NOT NULL, \n\ttask_id TEXT NOT NULL, \n\tsequence BIGINT NOT NULL, \n\ttype TEXT NOT NULL, \n\tidempotency_key TEXT NOT NULL, \n\tpayload JSONB NOT NULL, \n\tenvelope JSONB NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (task_id, sequence), \n\tUNIQUE (task_id, idempotency_key), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id)\n)\n\n')
    op.execute('CREATE INDEX ix_events_task_id ON events (task_id)')
    op.execute('\nCREATE TABLE memory_jobs (\n\tid TEXT NOT NULL, \n\ttask_id TEXT NOT NULL, \n\tscope_key TEXT NOT NULL, \n\tfrom_seq BIGINT NOT NULL, \n\tthrough_seq BIGINT NOT NULL, \n\tstatus TEXT NOT NULL, \n\trun_after TIMESTAMP WITH TIME ZONE NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\tlease_token TEXT, \n\tlast_error TEXT, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id)\n)\n\n')
    op.execute('CREATE INDEX ix_memory_jobs_task_id ON memory_jobs (task_id)')
    op.execute('CREATE INDEX ix_memory_due ON memory_jobs (status, run_after)')
    op.execute('\nCREATE TABLE operations (\n\tid TEXT NOT NULL, \n\ttask_id TEXT NOT NULL, \n\tnode TEXT NOT NULL, \n\trequest_hash TEXT NOT NULL, \n\tstatus TEXT NOT NULL, \n\tpatch JSONB, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id)\n)\n\n')
    op.execute('CREATE INDEX ix_operations_task_id ON operations (task_id)')
    op.execute('\nCREATE TABLE entity_records (\n\ttask_id TEXT NOT NULL, \n\tentity_id TEXT NOT NULL, \n\tkind TEXT NOT NULL, \n\tpayload JSONB NOT NULL, \n\tsource_event_id TEXT NOT NULL, \n\tPRIMARY KEY (task_id, entity_id), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(source_event_id) REFERENCES events (id)\n)\n\n')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS checkpoints")
    op.execute("""CREATE FUNCTION reject_event_mutation() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION 'events is append-only'; END;
    $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON events FOR EACH STATEMENT EXECUTE FUNCTION reject_event_mutation()")


def downgrade():
    op.execute("DROP TRIGGER events_append_only ON events")
    op.execute("DROP FUNCTION reject_event_mutation()")
    op.drop_table('entity_records')
    op.drop_table('operations')
    op.drop_table('memory_jobs')
    op.drop_table('events')
    op.drop_table('tasks')
    op.drop_table('projects')
    op.drop_table('users')
    op.drop_table('memory_receipts')
    op.drop_table('api_receipts')
