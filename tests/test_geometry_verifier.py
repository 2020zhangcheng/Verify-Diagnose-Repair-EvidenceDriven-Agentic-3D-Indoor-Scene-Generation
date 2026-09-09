from math import cos, pi, sin

import pytest

from app.contracts.models import Box, CameraPose
from app.repair.models import RepairRequest, RepairToolSelection
from app.repair.registry import default_repair_tool_registry
from app.verification.geometry import DeterministicGeometryVerifier, penetration
from app.verification.models import GeometryObject, SceneSnapshot


def obj(
    ident,
    x=0,
    y=0,
    z=0.5,
    size=(1, 1, 1),
    support="floor",
    movable=True,
    rotation=(0, 0, 0, 1),
    **kwargs,
):
    return GeometryObject(
        object_id=ident,
        geometry=Box(
            pose=CameraPose(frame_id="world", position_m=(x, y, z), orientation_xyzw=rotation),
            size_m=size,
        ),
        support_id=support,
        movable=movable,
        **kwargs,
    )


def scene(*objects):
    return SceneSnapshot(scene_id="test", objects=objects)


def diagnostics(value, rule):
    return [item for item in DeterministicGeometryVerifier().diagnose(value).diagnostics if item.rule_id == rule]


def test_collision_depth_and_volume():
    diagnostic = diagnostics(scene(obj("a"), obj("b", x=0.8)), "collision")[0]
    assert diagnostic.status == "fail"
    assert diagnostic.measurements["penetration_depth_m"] == pytest.approx(0.2)
    assert diagnostic.measurements["intersection_volume_m3"] == pytest.approx(0.2)
    assert diagnostic.allowed_repair_tools == ("resolve_collision",)


@pytest.mark.parametrize("x, expected", [(1, "pass"), (1 - 1e-7, "pass"), (0.999, "fail"), (2, "pass")])
def test_contact_tolerance(x, expected):
    assert diagnostics(scene(obj("a"), obj("b", x=x)), "collision")[0].status == expected


def test_containment_translation_separates():
    solutions = penetration(obj("a", size=(4, 4, 4), z=2).geometry, obj("b", z=2).geometry, 1e-6)
    assert solutions[0][0] == pytest.approx(2.5)


def test_rotated_collision_is_supported_by_sat_but_support_is_unknown():
    rotation = (0, 0, sin(pi / 8), cos(pi / 8))
    value = scene(obj("a", rotation=rotation), obj("b", x=1.1))
    assert diagnostics(value, "collision")[0].status == "fail"
    assert diagnostics(value, "collision")[0].measurements["intersection_volume_m3"] is None
    assert diagnostics(value, "support")[0].status == "unknown"


def test_floating_object_has_support_tool():
    diagnostic = diagnostics(scene(obj("a", z=0.7)), "support")[0]
    assert diagnostic.status == "fail"
    assert diagnostic.measurements["gap_m"] == pytest.approx(0.2)
    assert diagnostic.allowed_repair_tools == ("repair_support_contact",)


def test_stabilize_support_uses_editable_object_and_leaves_anchor_locked():
    value = scene(obj("base", movable=False, anchored=True), obj("vase", x=0.6, z=1.25, size=(0.5, 0.5, 0.5), support="base"))
    report = DeterministicGeometryVerifier().diagnose(value)
    diagnostic = next(item for item in report.diagnostics if item.rule_id == "support" and item.object_ids[0] == "vase")
    assert diagnostic.status == "fail"
    assert diagnostic.allowed_repair_tools == ("stabilize_support",)
    assert diagnostic.locked_objects == ("base",)
    registry = default_repair_tool_registry()
    selection = RepairToolSelection(
        selected_diagnosis_id=diagnostic.diagnosis_id,
        tool="stabilize_support",
        arguments={"object_id": "vase", "strategy": "center_on_support"},
        reason_code="test",
    )
    outcome = registry.execute(value, report, selection)
    assert outcome.action.object_id == "vase"
    assert outcome.scene.objects[1].geometry.pose.position_m[0] == pytest.approx(0)


def test_stale_tool_request_is_rejected():
    value = scene(obj("a", z=0.7))
    report = DeterministicGeometryVerifier().diagnose(value)
    diagnostic = next(item for item in report.diagnostics if item.rule_id == "support")
    selection = RepairToolSelection(
        selected_diagnosis_id=diagnostic.diagnosis_id,
        tool="repair_support_contact",
        arguments={"object_id": "a"},
        reason_code="test",
    )
    stale = value.model_copy(update={"scene_id": "different"})
    tool = default_repair_tool_registry().get("repair_support_contact")
    request = RepairRequest(
        diagnosis_id=diagnostic.diagnosis_id,
        scene_revision=value.revision,
        object_id="a",
    )
    with pytest.raises(ValueError, match="stale_scene_revision"):
        tool.solve(stale, diagnostic, request)


def test_fixed_floating_object_has_no_editable_target():
    value = scene(obj("a", z=2, movable=False))
    report = DeterministicGeometryVerifier().diagnose(value)
    assert report.status == "fail"
    assert all(not diagnostic.editable_objects for diagnostic in report.diagnostics if diagnostic.status == "fail")


def test_scene_validation_rejects_duplicate_and_self_support():
    with pytest.raises(ValueError, match="duplicate"):
        scene(obj("a"), obj("a"))
    with pytest.raises(ValueError, match="self support"):
        scene(obj("a", support="a"))
