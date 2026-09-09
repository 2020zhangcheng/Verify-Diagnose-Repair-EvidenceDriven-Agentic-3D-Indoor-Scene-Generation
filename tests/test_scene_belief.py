import asyncio
from datetime import timedelta
import pytest
from pydantic import ValidationError
from app.contracts.models import Observation, TaskSpec, SceneObject, Claim, EvidenceRef, SceneBelief, Uncertainty
from app.environment.fake import FakeEnvironmentAdapter
from app.contracts.models import OperationContext
from app.scene.fixtures import structured_observation, box
from app.scene.belief import StructuredSceneBeliefService, InMemoryObservationArchive, FusionInputError
from app.scene.config import load_config


def observation(number=1):
    ctx = OperationContext(operation_id=f"op-{number}", task_id="task", run_id="run", expected_environment_revision="fake-room-1", fencing_token=1)
    base = asyncio.run(FakeEnvironmentAdapter(number).observe(ctx))
    return structured_observation(base, number)


def task():
    return TaskSpec(task_id="task", objective="Move desk by window", constraints=(), seed=0, config_id="scene-belief-v1")


def fuse(*observations, config=None):
    return asyncio.run(StructuredSceneBeliefService(config=config).update(None, observations, task()))


def incremental(first, second):
    archive = InMemoryObservationArchive((first, second))
    service = StructuredSceneBeliefService(archive.load)
    old = asyncio.run(service.update(None, (first,), task()))
    new = asyncio.run(service.update(old, (second,), task()))
    return old, new, service


def test_initial_partial_scene_has_no_invented_free_space():
    belief = fuse(observation())
    assert belief.objects[0].geometry is not None
    assert {r.region_id: r.kind for r in belief.regions} == {"behind-cabinet": "unobserved", "window-region": "occluded"}
    assert belief.claims[0].value is None and belief.claims[0].status == "uncertain"
    assert not belief.room_geometry.complete
    assert len(belief.room_geometry.floors) == 1
    assert belief.objects[0].uncertainty.observation_count == 1


def test_new_evidence_fuses_geometry_and_supports_claim():
    first, second, _ = incremental(observation(), observation(2))
    assert first.objects[0].geometry.pose.position_m[0] == 0  # prior immutable
    assert 0 < second.objects[0].geometry.pose.position_m[0] < .04
    assert second.parent == first.ref and second.ref.version == 2
    assert second.objects[0].uncertainty.observation_count == 2
    assert second.objects[0].uncertainty.aggregate < first.objects[0].uncertainty.aggregate
    assert second.claims[0].status == "supported" and second.claims[0].value is True
    assert next(r for r in second.regions if r.region_id == "behind-cabinet").kind == "unobserved"
    refs = second.objects[0].evidence
    assert {r.observation_id for r in refs} == {observation().observation_id, observation(2).observation_id}


def test_duplicate_replay_does_not_increment_version_or_count():
    a, b = observation(), observation(2)
    _, belief, service = incremental(a, b)
    assert asyncio.run(service.update(belief, (a, b, b), task())) is belief
    assert fuse(a, a, a) == fuse(a)


def test_same_pose_different_image_ids_is_one_view():
    a, b = observation(), observation(2)
    b = b.model_copy(update={"camera_pose": a.camera_pose, "detected_objects": a.detected_objects})
    assert fuse(a, b).objects[0].uncertainty.observation_count == 1
    q = a.camera_pose.model_copy(update={"orientation_xyzw": (0,0,0,-1)})
    b = b.model_copy(update={"camera_pose": q})
    assert fuse(a, b).objects[0].uncertainty.observation_count == 1


def test_permutation_and_batch_boundaries_preserve_content():
    a, b = observation(), observation(2)
    batch = fuse(a, b)
    assert batch == fuse(b, a)
    _, incremental_belief, _ = incremental(a, b)
    assert batch.model_dump(exclude={"ref", "parent"}) == incremental_belief.model_dump(exclude={"ref", "parent"})


def test_empty_detection_does_not_delete_previous_object():
    a, b = observation(), observation(2)
    b = b.model_copy(update={"detected_objects": (), "observed_regions": (), "observed_claims": ()})
    old, new, _ = incremental(a, b)
    assert new.objects == old.objects
    assert next(r for r in new.regions if r.region_id == "window-region").kind == "occluded"


@pytest.mark.parametrize("field,change", [("geometry", box(x=3)), ("semantic_class", "radiator")])
def test_conflicts_are_explicit_and_not_averaged_away(field, change):
    a, b = observation(), observation(2)
    bad = b.detected_objects[0].model_copy(update={field: change})
    b = b.model_copy(update={"detected_objects": (bad,)})
    belief = fuse(a, b)
    obj = belief.objects[0]
    assert obj.knowledge == "conflicted"
    assert obj.uncertainty.cross_view_inconsistency == 1
    assert obj.uncertainty.geometry_uncertainty == 1
    assert len(obj.evidence) == 2
    if field == "geometry":
        assert obj.geometry.pose.position_m[0] in (0, 3)
    else:
        assert obj.semantic_class is None


def test_region_free_occupied_conflict_prevents_supported_empty_claim():
    a, b = observation(2), observation(3)
    b = b.model_copy(update={"camera_pose": b.camera_pose.model_copy(update={"position_m": (2,0,1.5)}),
        "observed_regions": (b.observed_regions[0].model_copy(update={"kind": "occupied"}),),
        "observed_claims": (b.observed_claims[0].model_copy(update={"value": False}),)})
    belief = fuse(a,b)
    region = next(r for r in belief.regions if r.region_id == "window-region")
    assert region.kind == "uncertain" and region.uncertainty.cross_view_inconsistency == 1
    assert belief.claims[0].status == "conflicted" and belief.claims[0].value is None


def test_claim_cannot_assert_empty_without_visible_region_evidence():
    a = observation()
    a = a.model_copy(update={"observed_claims": (a.observed_claims[0].model_copy(update={"status": "supported", "value": True, "confidence": .99}),)})
    assert fuse(a).claims[0].status == "uncertain"
    region = a.observed_regions[0].model_copy(update={"kind": "free"})
    a = a.model_copy(update={"observed_regions": (region,)})
    assert fuse(a).regions[0].kind == "uncertain"


def test_changed_region_extent_does_not_promote_whole_space_to_free():
    a, b = observation(), observation(2)
    region = b.observed_regions[0].model_copy(update={"bounds": box(x=1, size=(.2,.2,.2))})
    b = b.model_copy(update={"observed_regions": (region,)})
    assert next(r for r in fuse(a,b).regions if r.region_id == "window-region").kind == "uncertain"


@pytest.mark.parametrize("change", [{"task_id": "other"}, {"environment_revision": "other"}, {"camera_pose": observation().camera_pose.model_copy(update={"frame_id": "other"})}])
def test_rejects_mixed_task_frame_epoch(change):
    with pytest.raises(FusionInputError):
        fuse(observation(), observation(2).model_copy(update=change))


@pytest.mark.parametrize("ref", [EvidenceRef(observation_id="other", camera_view_id="other"),
    EvidenceRef(observation_id="op-1:obs", camera_view_id="op-1:camera", artifact_id="missing")])
def test_rejects_forged_evidence(ref):
    a = observation()
    a = a.model_copy(update={"detected_objects": (a.detected_objects[0].model_copy(update={"evidence": (ref,)}),)})
    with pytest.raises(FusionInputError, match="evidence"):
        fuse(a)


def test_rejects_duplicate_id_with_new_payload_and_missing_history():
    a = observation()
    with pytest.raises(FusionInputError, match="ID reused"):
        fuse(a, a.model_copy(update={"detected_objects": ()}))
    prior = fuse(a)
    with pytest.raises(FusionInputError, match="loader"):
        asyncio.run(StructuredSceneBeliefService().update(prior, (observation(2),), task()))
    with pytest.raises(FusionInputError, match="missing"):
        asyncio.run(StructuredSceneBeliefService(InMemoryObservationArchive().load).update(prior, (), task()))


def test_missing_features_stay_none_and_configuration_is_effective():
    a = observation()
    obj = a.detected_objects[0].model_copy(update={"geometry": None, "uncertainty": Uncertainty(estimator_version="test")})
    a = a.model_copy(update={"detected_objects": (obj,)})
    belief = fuse(a)
    u = belief.objects[0].uncertainty
    assert u.geometry_confidence is None and u.geometry_uncertainty is None
    assert u.visibility is None and u.occlusion_ratio is None
    assert u.aggregate > 0
    changed = load_config().model_copy(update={"missing_feature_risk": .5})
    other = fuse(a, config=changed)
    assert other.objects[0].uncertainty.aggregate < u.aggregate
    assert other.fusion_version != belief.fusion_version


def test_old_observation_schema_still_loads_and_belief_roundtrips():
    raw = observation().model_dump(mode="json")
    for key in ("observed_regions", "observed_claims", "observed_room_geometry"):
        raw.pop(key)
    old = Observation.model_validate(raw)
    assert old.observed_regions == ()
    belief = fuse(old)
    assert not belief.regions and not belief.room_geometry.complete
    assert SceneBelief.model_validate_json(belief.model_dump_json()) == belief


def test_invalid_config_rejected():
    config = load_config().model_dump(mode="json")
    config["position_tolerance_m"] = 0
    with pytest.raises(ValidationError):
        type(load_config()).model_validate(config)


def test_unobserved_region_keeps_missing_conflict_feature_unknown():
    region = next(r for r in fuse(observation()).regions if r.kind == "unobserved")
    assert region.uncertainty.cross_view_inconsistency is None
    assert region.uncertainty.observation_count == 0
    assert region.uncertainty.aggregate == 1


def test_changed_duplicate_rejected_against_immutable_history():
    a, b = observation(), observation(2)
    old, _, service = incremental(a, b)
    changed = a.model_copy(update={"detected_objects": ()})
    with pytest.raises(FusionInputError, match="ID reused"):
        asyncio.run(service.update(old, (changed,), task()))


def test_prior_and_new_config_cannot_be_mixed():
    a = observation()
    previous = fuse(a)
    changed = load_config().model_copy(update={"position_tolerance_m": .5})
    with pytest.raises(FusionInputError, match="config mismatch"):
        asyncio.run(StructuredSceneBeliefService(InMemoryObservationArchive((a,)).load, changed).update(previous, (), task()))


def test_tampered_prior_rejected():
    a, b = observation(), observation(2)
    old, _, service = incremental(a,b)
    bad = old.model_copy(update={"objects": ()})
    with pytest.raises(FusionInputError, match="does not match"):
        asyncio.run(service.update(bad, (b,), task()))


def test_empty_batch_and_new_empty_scene_are_distinct():
    with pytest.raises(FusionInputError, match="at least one"):
        fuse()
    a = observation().model_copy(update={"detected_objects": (), "observed_regions": (), "observed_claims": (), "observed_room_geometry": None})
    belief = fuse(a)
    assert belief.objects == belief.regions == belief.claims == ()
    assert not belief.room_geometry.complete


def test_new_region_measurement_resolves_existing_claim_without_claim_label():
    a, b = observation(), observation(2)
    b = b.model_copy(update={"observed_claims": ()})
    _, updated, _ = incremental(a,b)
    claim = next(c for c in updated.claims if c.claim_id == "window-space-empty")
    assert claim.value is True and claim.status == "supported"
    assert {r.observation_id for r in claim.evidence} == {a.observation_id, b.observation_id}
