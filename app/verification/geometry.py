"""Deterministic box geometry checks used as the Geometry Critic."""

from itertools import combinations
from math import prod, sqrt
from pathlib import Path

from app.environment.geometry import axes, corners, cross, dot, rotate, sub
from app.repair.catalog import allowed_repair_tools_for
from app.verification.models import (
    Diagnostic,
    DiagnosisReport,
    SceneSnapshot,
    VerifierConfig,
    fingerprint,
)


def load_config() -> VerifierConfig:
    path = Path(__file__).resolve().parents[2] / "configs" / "geometry-verifier-v1.json"
    return VerifierConfig.model_validate_json(path.read_text())


def bounds(box):
    points = corners(box)
    return (
        tuple(min(point[index] for point in points) for index in range(3)),
        tuple(max(point[index] for point in points) for index in range(3)),
    )


def aligned(box, tolerance: float) -> bool:
    return all(max(abs(value) for value in axis) >= 1 - tolerance for axis in axes(box.pose))


def penetration(a, b, tolerance: float):
    """Return minimum separating candidates, or ``None`` when not colliding."""

    axes_a, axes_b = axes(a.pose), axes(b.pose)
    delta = sub(b.pose.position_m, a.pose.position_m)
    solutions = []
    for candidate in (*axes_a, *axes_b, *(cross(first, second) for first in axes_a for second in axes_b)):
        norm = sqrt(dot(candidate, candidate))
        if norm < 1e-10:
            continue
        axis = tuple(value / norm for value in candidate)
        radius = sum(size / 2 * abs(dot(axis, value)) for size, value in zip(a.size_m, axes_a))
        radius += sum(size / 2 * abs(dot(axis, value)) for size, value in zip(b.size_m, axes_b))
        distance = dot(delta, axis)
        depth = radius - abs(distance)
        if depth <= tolerance:
            return None
        direction = -1 if distance >= 0 else 1
        solutions.append((depth, tuple(direction * depth * value for value in axis)))
    return sorted(set(solutions))


class DeterministicGeometryVerifier:
    """Repeatable critic for collisions, floor contact, support and support chains."""

    def __init__(self, config: VerifierConfig | None = None):
        self.config = config or load_config()
        self.version = "box-verifier-v1:" + fingerprint(self.config)

    def diagnose(self, scene: SceneSnapshot) -> DiagnosisReport:
        # Revalidate a copy so callers cannot mutate a report's source scene
        # while the loop is between Critic, Router and Tool stages.
        scene = SceneSnapshot.model_validate(scene.model_dump(mode="json"))
        config = self.config
        diagnostics: list[Diagnostic] = []
        objects = {obj.object_id: obj for obj in scene.objects}

        def add(rule, ids, status, reason, measurements, amount=0):
            diagnosis_id = rule + ":" + ",".join(ids)
            editable = tuple(object_id for object_id in ids if object_id in objects and objects[object_id].movable)
            locked = tuple(object_id for object_id in ids if object_id in objects and not objects[object_id].movable)
            allowed = allowed_repair_tools_for(
                rule,
                status,
                measurements,
                contact_tolerance_m=config.contact_tolerance_m,
            )
            diagnostic = Diagnostic(
                diagnosis_id=diagnosis_id,
                rule_id=rule,
                status=status,
                object_ids=ids,
                reason=reason,
                measurements=measurements,
                severity=min(1, max(0, amount) / config.severity_scale_m),
                editable_objects=editable,
                locked_objects=locked,
                allowed_repair_tools=allowed,
            )
            diagnostics.append(diagnostic)
            return diagnostic

        for first, second in combinations(sorted(scene.objects, key=lambda item: item.object_id), 2):
            solutions = penetration(first.geometry, second.geometry, config.penetration_tolerance_m)
            volume = None
            if aligned(first.geometry, config.axis_tolerance) and aligned(second.geometry, config.axis_tolerance):
                first_low, first_high = bounds(first.geometry)
                second_low, second_high = bounds(second.geometry)
                volume = prod(
                    max(0, min(first_high[index], second_high[index]) - max(first_low[index], second_low[index]))
                    for index in range(3)
                )
            add(
                "collision",
                (first.object_id, second.object_id),
                "fail" if solutions else "pass",
                "Solid boxes interpenetrate" if solutions else "No box penetration above tolerance",
                {
                    "penetration_depth_m": solutions[0][0] if solutions else 0,
                    "intersection_volume_m3": volume,
                    "tolerance_m": config.penetration_tolerance_m,
                },
                solutions[0][0] if solutions else 0,
            )

        local: dict[str, Diagnostic] = {}
        for obj in sorted(scene.objects, key=lambda item: item.object_id):
            low, high = bounds(obj.geometry)
            depth = scene.floor_z_m - low[2]
            add(
                "floor_penetration",
                (obj.object_id, "floor"),
                "fail" if depth > config.penetration_tolerance_m else "pass",
                "Object penetrates floor" if depth > config.penetration_tolerance_m else "Object is above floor boundary",
                {
                    "penetration_depth_m": max(0, depth),
                    "tolerance_m": config.penetration_tolerance_m,
                },
                max(0, depth),
            )
            if obj.anchored or not obj.requires_support:
                continue

            support = None
            if obj.support_id is not None and obj.support_id != "floor":
                support = objects.get(obj.support_id)
            ids = (obj.object_id,) if obj.support_id is None else (obj.object_id, obj.support_id)
            if obj.support_id is None or (obj.support_id != "floor" and support is None):
                local[obj.object_id] = add(
                    "support",
                    ids,
                    "unknown" if obj.support_id is None else "fail",
                    "Support intent missing" if obj.support_id is None else "Declared support object missing",
                    {},
                    config.severity_scale_m,
                )
                continue
            if not aligned(obj.geometry, config.axis_tolerance) or (support and not aligned(support.geometry, config.axis_tolerance)):
                local[obj.object_id] = add(
                    "support",
                    ids,
                    "unknown",
                    "Rotated support geometry is outside V1 capability",
                    {},
                )
                continue

            com_offset = rotate(obj.center_of_mass_local_m or (0, 0, 0), obj.geometry.pose)
            com = tuple(position + offset for position, offset in zip(obj.geometry.pose.position_m, com_offset))
            top = scene.floor_z_m
            area = (high[0] - low[0]) * (high[1] - low[1])
            margin = None
            dx = dy = 0.0
            if support:
                support_low, support_high = bounds(support.geometry)
                intervals = [(max(low[index], support_low[index]), min(high[index], support_high[index])) for index in range(2)]
                area = prod(max(0, high_value - low_value) for low_value, high_value in intervals)
                margin = min(com[index] - intervals[index][0] for index in range(2))
                margin = min(margin, *(intervals[index][1] - com[index] for index in range(2)))
                dx, dy = ((support_low[index] + support_high[index]) / 2 - com[index] for index in range(2))
                top = support_high[2]
            gap = low[2] - top
            contact = abs(gap) <= config.contact_tolerance_m and area > 0
            good_margin = margin is None or margin >= config.minimum_support_margin_m
            valid = contact and good_margin
            reason = (
                "Contact and center-of-mass projection satisfy geometric support rules"
                if valid
                else "Floating above declared support"
                if gap > config.contact_tolerance_m
                else "Penetrates declared support"
                if gap < -config.contact_tolerance_m
                else "Insufficient contact area or center-of-mass support margin"
            )
            measurements = {
                "gap_m": gap,
                "contact": contact,
                "contact_area_m2": area if contact else 0,
                "projected_overlap_area_m2": area,
                "support_margin_m": margin,
                "minimum_margin_m": config.minimum_support_margin_m,
                "contact_tolerance_m": config.contact_tolerance_m,
                "center_of_mass_assumed": obj.center_of_mass_local_m is None,
            }
            local[obj.object_id] = add(
                "support",
                ids,
                "pass" if valid else "fail",
                reason,
                measurements,
                0 if valid else max(abs(gap), max(0, config.minimum_support_margin_m - (margin or 0))),
            )

        def grounded(object_id, seen):
            if object_id == "floor":
                return "pass"
            obj = objects.get(object_id)
            if obj is None:
                return "unknown"
            if obj.anchored:
                return "pass"
            if object_id in seen or object_id not in local:
                return "unknown"
            if local[object_id].status != "pass":
                return local[object_id].status
            return grounded(obj.support_id, seen | {object_id})

        for obj in sorted(scene.objects, key=lambda item: item.object_id):
            if obj.object_id in local and local[obj.object_id].status == "pass":
                status = grounded(obj.object_id, set())
                add(
                    "support_chain",
                    (obj.object_id,),
                    status,
                    "Support chain reaches floor or declared anchor"
                    if status == "pass"
                    else "Support chain is not verified as grounded",
                    {},
                )

        status = (
            "fail"
            if any(item.status == "fail" for item in diagnostics)
            else "unknown"
            if any(item.status == "unknown" for item in diagnostics)
            else "pass"
        )
        return DiagnosisReport(
            scene_revision=scene.revision,
            verifier_version=self.version,
            status=status,
            diagnostics=tuple(diagnostics),
            assumptions=(
                "Objects are solid boxes; dimensions are meters in a common Z-up frame.",
                "Missing center of mass assumes uniform solid box.",
                "Support checks are geometric necessary conditions, not a dynamics certificate.",
            ),
        )
