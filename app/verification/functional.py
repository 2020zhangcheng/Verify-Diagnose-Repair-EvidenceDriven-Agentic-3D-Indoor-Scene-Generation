"""Deterministic Functional Critic for optional interaction constraints.

The Geometry Critic remains the hard geometric gate.  This module is the
second, optional layer described by the Functional Critic model: it checks
whether an object has at least one usable interaction pose under navigation,
approach, clearance, operation-sweep and relation constraints.

It deliberately has no LLM dependency.  The LLM may select a catalogued tool
after a diagnosis, while this module computes the functional verdict again
after every deterministic repair.
"""

from __future__ import annotations

from collections import deque
from math import acos, cos, degrees, hypot, pi, sin, sqrt
from pathlib import Path
from typing import Iterable

from app.contracts.models import Box
from app.environment.geometry import corners, rotate
from app.repair.catalog import allowed_repair_tools_for
from app.verification.models import (
    Affordance,
    Diagnostic,
    DiagnosisReport,
    FunctionalContext,
    FunctionalRelation,
    FunctionalVerifierConfig,
    MovePrescription,
    SceneSnapshot,
    fingerprint,
)


def load_config() -> FunctionalVerifierConfig:
    path = Path(__file__).resolve().parents[2] / "configs" / "functional-verifier-v1.json"
    return FunctionalVerifierConfig.model_validate_json(path.read_text())


def _bounds(box: Box):
    points = corners(box)
    return (
        tuple(min(point[index] for point in points) for index in range(3)),
        tuple(max(point[index] for point in points) for index in range(3)),
    )


def _overlap_volume(first: Box, second: Box) -> float:
    first_low, first_high = _bounds(first)
    second_low, second_high = _bounds(second)
    return prod(
        max(0.0, min(first_high[index], second_high[index]) - max(first_low[index], second_low[index]))
        for index in range(3)
    )


def prod(values: Iterable[float]) -> float:
    result = 1.0
    for value in values:
        result *= value
    return result


def _unit(vector):
    norm = sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return None
    return tuple(value / norm for value in vector)


def _horizontal_unit(vector):
    return _unit((vector[0], vector[1], 0.0))


def _anchor(obj, affordance: Affordance):
    offset = rotate(affordance.interaction_anchor_local_m, obj.geometry.pose)
    return tuple(position + delta for position, delta in zip(obj.geometry.pose.position_m, offset))


def _front(obj, affordance: Affordance):
    return _unit(rotate(affordance.front_axis_local, obj.geometry.pose))


def _candidate_positions(obj, affordance: Affordance, context: FunctionalContext | None):
    anchor = _anchor(obj, affordance)
    front = _horizontal_unit(_front(obj, affordance) or (0.0, -1.0, 0.0))
    lower, upper = affordance.approach_distance_m
    distances = tuple(sorted({lower, (lower + upper) / 2.0, upper}))
    max_angle = affordance.approach_angle_deg
    candidates = []
    for distance in distances:
        for angle in (-max_angle, 0.0, max_angle):
            radians = angle * pi / 180.0
            direction = (
                front[0] * cos(radians) - front[1] * sin(radians),
                front[0] * sin(radians) + front[1] * cos(radians),
            )
            candidates.append((anchor[0] - distance * direction[0], anchor[1] - distance * direction[1], 0.0))
    return tuple(candidates)


def _distance_xy(first, second):
    return hypot(first[0] - second[0], first[1] - second[1])


def _angle_deg(vector, target):
    first = _unit(vector)
    second = _unit(target)
    if first is None or second is None:
        return 180.0
    value = max(-1.0, min(1.0, sum(a * b for a, b in zip(first, second))))
    return degrees(acos(value))


def _point_free(scene: SceneSnapshot, point, *, excluded: set[str], context: FunctionalContext):
    radius = context.agent_radius_m + context.navigation_clearance_m
    if context.room_bounds_xy_m is not None:
        xmin, ymin, xmax, ymax = context.room_bounds_xy_m
        if not (xmin + radius <= point[0] <= xmax - radius and ymin + radius <= point[1] <= ymax - radius):
            return False
    for obj in scene.objects:
        if obj.object_id in excluded:
            continue
        low, high = _bounds(obj.geometry)
        if low[0] - radius <= point[0] <= high[0] + radius and low[1] - radius <= point[1] <= high[1] + radius:
            return False
    return True


def _reachable(scene: SceneSnapshot, target_id: str, goals):
    context = scene.functional_context
    if context is None or context.room_bounds_xy_m is None or context.agent_start_position_m is None:
        return None
    xmin, ymin, xmax, ymax = context.room_bounds_xy_m
    margin = context.agent_radius_m + context.navigation_clearance_m
    resolution = context.grid_resolution_m
    width = int((xmax - xmin - 2 * margin) / resolution) + 1
    height = int((ymax - ymin - 2 * margin) / resolution) + 1
    # A bounded grid keeps a malformed user room from causing an unbounded
    # synchronous request.  Larger rooms must be supplied at a coarser grid.
    if width < 1 or height < 1 or width > 400 or height > 400:
        return None

    def point(index):
        ix, iy = index
        return (xmin + margin + ix * resolution, ymin + margin + iy * resolution, 0.0)

    def nearest(value):
        ix = round((value[0] - (xmin + margin)) / resolution)
        iy = round((value[1] - (ymin + margin)) / resolution)
        return max(0, min(width - 1, ix)), max(0, min(height - 1, iy))

    blocked = set()
    excluded = {target_id}
    for ix in range(width):
        for iy in range(height):
            index = (ix, iy)
            if not _point_free(scene, point(index), excluded=excluded, context=context):
                blocked.add(index)

    start = nearest((*context.agent_start_position_m, 0.0))
    if start in blocked:
        return False
    goal_indices = {nearest(goal) for goal in goals}
    if goal_indices & {start}:
        return True
    queue = deque([start])
    visited = {start}
    while queue:
        current = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (current[0] + dx, current[1] + dy)
            if not (0 <= neighbor[0] < width and 0 <= neighbor[1] < height):
                continue
            if neighbor in visited or neighbor in blocked:
                continue
            if neighbor in goal_indices:
                return True
            visited.add(neighbor)
            queue.append(neighbor)
    return False


def _clearance_box(obj, affordance: Affordance, *, operation=False):
    size = affordance.operation_sweep_size_m if operation else affordance.clearance_size_m
    if size is None:
        return None
    return Box(
        pose=obj.geometry.pose.model_copy(update={"position_m": _anchor(obj, affordance)}),
        size_m=size,
    )


def _other_objects(scene: SceneSnapshot, object_id: str):
    return tuple(obj for obj in scene.objects if obj.object_id != object_id)


def _candidate_move_deltas(scene: SceneSnapshot, diagnosis: Diagnostic | str, target_id: str):
    target = next((obj for obj in scene.objects if obj.object_id == target_id), None)
    if target is None:
        return ()
    rule_id = diagnosis if isinstance(diagnosis, str) else diagnosis.rule_id
    candidates = []
    for step in (0.10, 0.25, 0.50, 1.0, 2.0):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            length = hypot(dx, dy)
            candidates.append((dx * step / length, dy * step / length, 0.0))

    if rule_id == "functional_relation":
        relation_object_ids = set(str(diagnosis).split(":", 1)[-1].split(",")) if not isinstance(diagnosis, str) else set()
        relation = next(
            (
                item
                for item in scene.functional_relations
                if not relation_object_ids
                or {item.subject_id, item.object_id} == relation_object_ids
            ),
            None,
        )
        if relation is not None:
            other_id = relation.object_id if relation.subject_id == target_id else relation.subject_id
            other = next((obj for obj in scene.objects if obj.object_id == other_id), None)
            if other is not None:
                direction = _unit(tuple(a - b for a, b in zip(target.geometry.pose.position_m, other.geometry.pose.position_m)))
                if direction is not None:
                    candidates.insert(0, tuple(value * 0.25 for value in direction))
    return tuple(candidates)


class FunctionalCritic:
    """Deterministic Functional Critic V1.

    A scene without affordances or functional relations is intentionally a
    functional PASS: the layer is opt-in and cannot turn legacy geometry-only
    scenes into false UNKNOWN results.
    """

    def __init__(self, config: FunctionalVerifierConfig | None = None):
        self.config = config or load_config()
        self.version = "functional-v1:" + fingerprint(self.config)

    def diagnose(self, scene: SceneSnapshot) -> DiagnosisReport:
        scene = SceneSnapshot.model_validate(scene.model_dump(mode="json"))
        objects = {obj.object_id: obj for obj in scene.objects}
        diagnostics: list[Diagnostic] = []
        context = scene.functional_context

        def add(rule, ids, status, reason, measurements, amount=0.0, suggestions=()):
            diagnosis_id = rule + ":" + ",".join(ids)
            editable = tuple(item for item in ids if item in objects and objects[item].movable)
            locked = tuple(item for item in ids if item in objects and not objects[item].movable)
            # A diagnosis should expose a compact solution set; the tool may
            # still search a larger deterministic candidate space internally.
            moves = tuple(
                MovePrescription(object_id=object_id, delta_m=delta, diagnosis_id=diagnosis_id)
                for object_id, delta in suggestions
                if object_id in objects and objects[object_id].movable
            )[:8]
            scale = self.config.angle_severity_scale_deg if "angle" in measurements else self.config.distance_severity_scale_m
            if "blocked_ratio" in measurements:
                scale = 1.0
            diagnostics.append(
                Diagnostic(
                    diagnosis_id=diagnosis_id,
                    rule_id=rule,
                    status=status,
                    object_ids=ids,
                    reason=reason,
                    measurements=measurements,
                    severity=min(1.0, max(0.0, amount) / scale),
                    editable_variables=tuple(f"{item}.position_m" for item in editable),
                    suggestions=moves,
                    editable_objects=editable,
                    locked_objects=locked,
                    allowed_repair_tools=allowed_repair_tools_for(rule, status, measurements),
                )
            )
            return diagnostics[-1]

        affordance_items = sorted(
            ((obj, obj.affordance) for obj in scene.objects if obj.affordance is not None),
            key=lambda item: item[0].object_id,
        )
        for obj, affordance in affordance_items:
            anchor = _anchor(obj, affordance)
            candidates = _candidate_positions(obj, affordance, context)
            free_candidates = tuple(
                candidate
                for candidate in candidates
                if context is None
                or _point_free(scene, candidate, excluded={obj.object_id}, context=context)
            )
            lower, upper = affordance.approach_distance_m
            approach_measurements = {
                "candidate_count": float(len(free_candidates)),
                "required_distance_min_m": lower,
                "required_distance_max_m": upper,
                "approach_angle_max_deg": affordance.approach_angle_deg,
            }
            if not free_candidates:
                approach_status = "fail"
                approach_reason = "No collision-free approach pose satisfies the affordance"
            else:
                approach_status = "pass"
                approach_reason = "At least one collision-free approach pose satisfies distance and orientation"
            if context is not None and context.room_bounds_xy_m is not None and context.agent_start_position_m is not None:
                reachable = _reachable(scene, obj.object_id, free_candidates)
                navigation_status = "unknown" if reachable is None else ("pass" if reachable else "fail")
                add(
                    "functional_navigation",
                    (obj.object_id,),
                    navigation_status,
                    "A free path connects the agent start to an interaction pose"
                    if reachable
                    else "No free path connects the agent start to an interaction pose"
                    if reachable is False
                    else "Navigation grid is unavailable for the supplied room bounds",
                    {"reachable": reachable},
                    1.0 if reachable is False else 0.0,
                    ((obj.object_id, delta) for delta in _candidate_move_deltas(
                        scene, "functional_navigation", obj.object_id
                    )) if reachable is False else (),
                )
            elif context is not None:
                add(
                    "functional_navigation",
                    (obj.object_id,),
                    "unknown",
                    "Functional navigation requires room_bounds_xy_m and agent_start_position_m",
                    {"reachable": None},
                )

            add(
                "functional_approach",
                (obj.object_id,),
                approach_status,
                approach_reason,
                approach_measurements,
                max(0.0, lower - min((_distance_xy(anchor, candidate) for candidate in candidates), default=lower)),
                ((obj.object_id, delta) for delta in _candidate_move_deltas(
                    scene, "functional_approach", obj.object_id
                )) if approach_status == "fail" else (),
            )

            clearance = _clearance_box(obj, affordance)
            if clearance is not None:
                volume = prod(clearance.size_m)
                blocked = sum(_overlap_volume(clearance, other.geometry) for other in _other_objects(scene, obj.object_id))
                ratio = min(1.0, blocked / volume) if volume > 0 else 1.0
                status = "pass" if ratio <= (context.clearance_block_ratio_max if context else self.config.clearance_block_ratio_max) else "fail"
                add(
                    "functional_clearance",
                    (obj.object_id,),
                    status,
                    "Functional clearance is available" if status == "pass" else "Functional clearance is blocked",
                    {"clearance_volume_m3": volume, "blocked_volume_m3": blocked, "blocked_ratio": ratio},
                    ratio,
                    ((obj.object_id, delta) for delta in _candidate_move_deltas(
                        scene, "functional_clearance", obj.object_id
                    )) if status == "fail" else (),
                )

            sweep = _clearance_box(obj, affordance, operation=True)
            if sweep is not None:
                blocked = sum(_overlap_volume(sweep, other.geometry) for other in _other_objects(scene, obj.object_id))
                status = "pass" if blocked <= 1e-12 else "fail"
                add(
                    "operation_sweep_blocked",
                    (obj.object_id,),
                    status,
                    "Operation sweep is collision-free" if status == "pass" else "Operation sweep intersects another object",
                    {"blocked_volume_m3": blocked},
                    blocked,
                    ((obj.object_id, delta) for delta in _candidate_move_deltas(
                        scene, "operation_sweep_blocked", obj.object_id
                    )) if status == "fail" else (),
                )

            reach_radius = affordance.reach_radius_m or (
                context.default_reach_radius_m if context is not None else self.config.default_reach_radius_m
            )
            reach_candidates = free_candidates if context is not None else candidates
            if reach_candidates:
                closest = min(_distance_xy(anchor, candidate) for candidate in reach_candidates)
                status = "pass" if closest <= reach_radius else "fail"
                add(
                    "reach_unavailable",
                    (obj.object_id,),
                    status,
                    "Interaction anchor is inside the reach envelope" if status == "pass" else "Interaction anchor exceeds the reach envelope",
                    {"required_reach_radius_m": reach_radius, "closest_distance_m": closest},
                    max(0.0, closest - reach_radius),
                    ((obj.object_id, delta) for delta in _candidate_move_deltas(
                        scene, "reach_unavailable", obj.object_id
                    )) if status == "fail" else (),
                )

        for relation in sorted(scene.functional_relations, key=lambda item: item.relation_id):
            subject = objects.get(relation.subject_id)
            target = objects.get(relation.object_id)
            if subject is None or target is None:
                add(
                    "functional_relation",
                    tuple(item for item in (relation.subject_id, relation.object_id) if item in objects),
                    "unknown",
                    "Functional relation references a missing object",
                    {"distance_m": None, "distance_min_m": relation.distance_m[0], "distance_max_m": relation.distance_m[1]},
                )
                continue
            subject_point = _anchor(subject, subject.affordance) if subject.affordance else subject.geometry.pose.position_m
            target_point = _anchor(target, target.affordance) if target.affordance else target.geometry.pose.position_m
            distance = hypot(subject_point[0] - target_point[0], subject_point[1] - target_point[1])
            lower, upper = relation.distance_m
            distance_violation = max(0.0, lower - distance, distance - upper)
            angle = None
            angle_violation = 0.0
            if relation.angle_deg is not None and subject.affordance is not None:
                direction = tuple(a - b for a, b in zip(target_point, subject_point))
                front = _front(subject, subject.affordance)
                angle = _angle_deg(direction, front)
                angle_violation = max(0.0, angle - relation.angle_deg)
            status = "pass" if distance_violation <= 1e-12 and angle_violation <= 1e-12 else "fail"
            measurements = {
                "distance_m": distance,
                "distance_min_m": lower,
                "distance_max_m": upper,
                "angle_deg": angle,
                "angle_max_deg": relation.angle_deg,
            }
            target_ids = (relation.subject_id, relation.object_id)
            suggestions = _candidate_move_deltas(
                scene,
                "functional_relation",
                relation.subject_id if subject.movable else relation.object_id,
            )
            add(
                "functional_relation",
                target_ids,
                status,
                "Functional relation satisfies distance and orientation constraints"
                if status == "pass"
                else "Functional relation violates distance or orientation constraints",
                measurements,
                max(distance_violation, angle_violation),
                ((relation.subject_id if subject.movable else relation.object_id, delta) for delta in suggestions)
                if status == "fail"
                else (),
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
                "The human/agent is represented by a 2D navigation disk.",
                "Affordances define interaction anchors, approach direction and optional clearance/sweep boxes.",
                "PASS means the deterministic V1 checks found at least one modeled interaction pose; it is not a full ergonomics certificate.",
            ),
        )


class GeometryFunctionalCritic:
    """Run Geometry Critic first and Functional Critic only after geometry PASS."""

    def __init__(self, geometry_verifier=None, functional_verifier: FunctionalCritic | None = None):
        from app.verification.geometry import DeterministicGeometryVerifier

        self.geometry_verifier = geometry_verifier or DeterministicGeometryVerifier()
        self.functional_verifier = functional_verifier or FunctionalCritic()
        self.config = self.geometry_verifier.config
        self.version = self.geometry_verifier.version + "+" + self.functional_verifier.version

    def diagnose(self, scene: SceneSnapshot) -> DiagnosisReport:
        geometry = self.geometry_verifier.diagnose(scene)
        if geometry.status != "pass":
            return geometry
        functional = self.functional_verifier.diagnose(scene)
        return DiagnosisReport(
            scene_revision=scene.revision,
            verifier_version=self.version,
            status=functional.status,
            diagnostics=(*geometry.diagnostics, *functional.diagnostics),
            assumptions=(*geometry.assumptions, *functional.assumptions),
        )


__all__ = ["FunctionalCritic", "GeometryFunctionalCritic", "load_config"]
