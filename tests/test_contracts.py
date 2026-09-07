import json
import math
import pytest
from pydantic import ValidationError
from app.contracts.models import (
    CameraPose, Uncertainty, SceneBelief, BeliefRef, RoomGeometry,
    Observation, CriticalUnknown, CandidateView,
)
from app.contracts.events import Event
from app.contracts import interfaces
from app.agent.state import RoomScoutState


def pose():
    return CameraPose(frame_id="world", position_m=(0, 0, 1), orientation_xyzw=(0, 0, 0, 1))


@pytest.mark.parametrize("value", [-0.01, 1.01, math.nan, math.inf])
def test_invalid_uncertainty(value):
    with pytest.raises(ValidationError):
        Uncertainty(geometry_uncertainty=value, estimator_version="v1")


def test_pose_and_extra_fields():
    with pytest.raises(ValidationError):
        CameraPose(frame_id="world", position_m=(0, 0, 0), orientation_xyzw=(0, 0, 0, 2))
    with pytest.raises(ValidationError):
        Uncertainty(estimator_version="v1", chat="belief is not chat")


def test_partial_belief_round_trip():
    belief = SceneBelief(ref=BeliefRef(belief_id="b1", version=1), task_id="t1",
                         frame_id="world", environment_revision="r1",
                         room_geometry=RoomGeometry(), fusion_version="v1")
    assert SceneBelief.model_validate_json(belief.model_dump_json()) == belief
    assert not belief.room_geometry.complete
    assert Uncertainty(estimator_version="v1").geometry_confidence is None


def test_observation_event_round_trip_and_timezone():
    raw = dict(observation_id="o1", task_id="t1", camera_view_id="v1", camera_pose=pose(),
               captured_at="2026-09-07T05:00:00+00:00", environment_revision="r1",
               source="replay", artifacts=(), sensor_calibration_id="c1", operation_id="op1")
    obs = Observation(**raw)
    event = Event(event_id="e1", task_id="t1", project_id="p1", user_id="u1", sequence=1,
                  idempotency_key="k1", correlation_id="t1", occurred_at=obs.captured_at,
                  recorded_at=obs.captured_at, producer="test",
                  payload={"kind": "ENTITY_RECORDED", "entity": obs})
    restored = Event.model_validate_json(event.model_dump_json())
    assert restored == event
    assert isinstance(restored.payload.entity, Observation)
    raw["captured_at"] = "2026-09-07T05:00:00"
    with pytest.raises(ValidationError):
        Observation(**raw)


def test_decision_unknown_and_view_require_links():
    ref = BeliefRef(belief_id="b1", version=1)
    with pytest.raises(ValidationError):
        CriticalUnknown(unknown_id="u1", belief_ref=ref, claim_id="c1",
                        assumption_ids=("a1",), affected_layout_ids=("l1",), description="radiator",
                        possible_outcomes=(), decision_impact=.9, current_uncertainty=.8,
                        assessment_method="rule", assessment_version="v1")
    with pytest.raises(ValidationError):
        CandidateView(view_id="v1", belief_ref=ref, camera_pose=pose(), target_unknown_ids=())


def test_ports_import_without_infrastructure():
    for name in ("EnvironmentAdapter", "SceneBeliefService", "LayoutPlanner",
                 "CriticalUnknownDetector", "ViewGenerator", "ViewSelector",
                 "GeometryVerifier", "MemoryService"):
        assert getattr(interfaces, name)._is_protocol
    assert "scene_belief" in RoomScoutState.__required_keys__
    json.dumps(SceneBelief.model_json_schema())
    json.dumps(Event.model_json_schema())
