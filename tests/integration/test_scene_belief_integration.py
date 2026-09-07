"""Real PostgreSQL + LangGraph tests for the single non-Mock scene module."""
import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db.postgres import SessionLocal
from app.db.models.tables import EventRow, EntityRecord
from app.workers.agent_runner import run_task
from app.agent.graph import SimulatedCrash
from app.scene.belief import StructuredSceneBeliefService, InMemoryObservationArchive
from app.scene.config import FusionConfig
from app.contracts.models import Observation, SceneBelief, TaskSpec

pytestmark = pytest.mark.integration
AUTH = {"Authorization": "Bearer roomscout-local-demo"}


def create(client):
    response = client.post("/tasks", headers={**AUTH, "Idempotency-Key": str(uuid4())},
                           json={"request": "把书桌移动到窗户附近", "config_id": "scene-belief-v1"})
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


def rows(task_id):
    with SessionLocal() as session:
        return list(session.scalars(select(EventRow).where(EventRow.task_id == task_id).order_by(EventRow.sequence)))


def test_real_fusion_in_graph_and_exact_event_replay(client):
    ident = create(client)
    result = run_task(ident)
    assert result["stage"] == "finished"
    events = rows(ident)
    belief_events = [e for e in events if e.type == "BELIEF_UPDATED"]
    assert len(belief_events) == 2
    first, second = [SceneBelief.model_validate(e.payload["entity"]) for e in belief_events]
    assert first.objects[0].uncertainty.observation_count == 1
    assert second.objects[0].uncertainty.observation_count == 2
    assert 0 < second.objects[0].geometry.pose.position_m[0] < .04
    assert first.claims[0].status == "uncertain" and second.claims[0].status == "supported"
    assert first.fusion_version.startswith("scene-belief-v1:")
    observations = {e.payload["entity"]["observation_id"]: Observation.model_validate(e.payload["entity"])
                    for e in events if e.type == "OBSERVATION_CREATED"}
    positions = {e.payload["entity"]["observation_id"]: e.sequence for e in events if e.type == "OBSERVATION_CREATED"}
    for event, belief in zip(belief_events, (first, second)):
        for ref in [ref for obj in (*belief.objects, *belief.regions, *belief.claims) for ref in obj.evidence]:
            assert positions[ref.observation_id] < event.sequence
            assert ref.camera_view_id == observations[ref.observation_id].camera_view_id
    configs = [FusionConfig.model_validate(e.payload["arguments"]["fusion_config"]) for e in events
               if e.type == "TOOL_CALL" and e.payload["tool_name"] == "scene.update"]
    assert len(configs) == 2 and configs[0] == configs[1]
    spec = TaskSpec.model_validate(next(e.payload["entity"] for e in events if e.type == "TASK_UNDERSTOOD"))
    archive = InMemoryObservationArchive(observations.values())
    service = StructuredSceneBeliefService(archive.load, configs[0])
    replayed = None
    for expected in (first, second):
        new_ids = set(expected.observation_ids) - set(replayed.observation_ids if replayed else ())
        replayed = asyncio.run(service.update(replayed, tuple(observations[i] for i in sorted(new_ids)), spec))
        assert replayed == expected
    from app.scene.replay import replay_beliefs
    wire_events = [{"sequence": e.sequence, "type": e.type, "payload": e.payload} for e in events]
    assert asyncio.run(replay_beliefs(wire_events)) == (first, second)
    from copy import deepcopy
    corrupted = deepcopy(wire_events)
    call = next(e for e in corrupted if e["type"] == "TOOL_CALL" and e["payload"]["tool_name"] == "scene.update")
    call["payload"]["arguments"]["fusion_config"]["position_tolerance_m"] = 99
    from app.scene.belief import FusionInputError
    with pytest.raises(FusionInputError, match="hash mismatch"):
        asyncio.run(replay_beliefs(corrupted))
    response = client.get(f"/tasks/{ident}/trace", headers=AUTH)
    assert response.status_code == 200
    assert any(n["kind"] == "action" for n in response.json()["nodes"])


def test_belief_checkpoint_gap_reuses_committed_result(client):
    ident = create(client)
    with pytest.raises(SimulatedCrash):
        run_task(ident, crash_after="update_scene_belief")
    before = [e.id for e in rows(ident)]
    result = run_task(ident)
    assert result["stage"] == "finished"
    after = rows(ident)
    assert [e.id for e in after[:len(before)]] == before
    assert sum(e.type == "BELIEF_UPDATED" for e in after) == 2
    assert sum(e.type == "ACTION_EXECUTED" for e in after) == 1


def test_without_new_spatial_evidence_belief_does_not_follow_mock_round_counter(client, monkeypatch):
    from app.scene import fixtures
    original = fixtures.structured_observation
    monkeypatch.setattr(fixtures, "structured_observation", lambda base, n: original(base, 1))
    ident = create(client)
    result = run_task(ident)
    assert result["stage"] == "blocked"
    events = rows(ident)
    beliefs = [SceneBelief.model_validate(e.payload["entity"]) for e in events if e.type == "BELIEF_UPDATED"]
    assert len(beliefs) == 2
    assert all(b.claims[0].status == "uncertain" for b in beliefs)
    assert not any(e.type == "ACTION_EXECUTED" for e in events)


def test_historical_optional_fields_remain_idempotent_without_rewriting_events(client):
    from app.db.repositories.store import locked_task, append_event, save_entity
    from app.contracts.events import Event
    from app.scene.fixtures import structured_observation
    from app.environment.fake import FakeEnvironmentAdapter
    from app.contracts.models import OperationContext
    ident = create(client)
    ctx = OperationContext(operation_id="legacy-op", task_id=ident, run_id="legacy", expected_environment_revision="fake-room-1", fencing_token=1)
    obs = asyncio.run(FakeEnvironmentAdapter().observe(ctx))
    payload = {"schema_version": 1, "kind": "ENTITY_RECORDED", "entity": obs.model_dump(mode="json")}
    for field in ("observed_regions", "observed_claims", "observed_room_geometry"):
        payload["entity"].pop(field)
    # Insert a historically valid row, not an UPDATE of the append-only log.
    with SessionLocal.begin() as session:
        task = locked_task(session, ident)
        task.sequence += 1
        event = Event(event_id=str(uuid4()), task_id=ident, project_id="demo-project", user_id="demo-user",
                      sequence=task.sequence, idempotency_key="legacy-observation", correlation_id=ident,
                      occurred_at=obs.captured_at, recorded_at=obs.captured_at, producer="legacy-fixture", payload=payload)
        envelope = event.model_dump(mode="json")
        envelope["payload"] = payload  # The original schema-v1 serialization omitted the new fields.
        row = EventRow(id=event.event_id, task_id=ident, sequence=task.sequence, type="OBSERVATION_CREATED",
                       idempotency_key="legacy-observation", payload=payload, envelope=envelope)
        session.add(row)
        session.flush()
        session.add(EntityRecord(task_id=ident, entity_id=obs.observation_id, kind="Observation", payload=payload["entity"], source_event_id=row.id))
        original_id = row.id
    with SessionLocal.begin() as session:
        task = locked_task(session, ident)
        saved = save_entity(session, task, obs, "OBSERVATION_CREATED", "legacy-observation")
        assert saved.source_event_id == original_id
    with SessionLocal() as session:
        stored = session.get(EventRow, original_id)
        assert "observed_regions" not in stored.payload["entity"]
        assert sum(e.id == original_id for e in rows(ident)) == 1
