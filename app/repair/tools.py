"""Deterministic V0 repair tools.

Every tool receives a diagnosis and a small semantic request.  It computes the
transform from the scene geometry and returns a new immutable SceneSnapshot plus
an auditable RepairAction.  There is intentionally no generic translate/place
tool in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import sqrt
from typing import Any

from app.environment.geometry import rotate
from app.repair.models import RepairAction, RepairRequest
from app.verification.geometry import aligned, bounds, penetration
from app.verification.models import Diagnostic, SceneSnapshot


class DeterministicToolError(ValueError):
    """A request cannot be solved without guessing or violating a guard."""


@dataclass(frozen=True)
class ToolConfig:
    penetration_tolerance_m: float = 0.000001
    contact_tolerance_m: float = 0.001
    minimum_support_margin_m: float = 0.001
    maximum_move_m: float = 2.0


@dataclass(frozen=True)
class ToolOutcome:
    scene: SceneSnapshot
    action: RepairAction


class DeterministicRepairTool:
    name = ""

    def can_handle(self, diagnosis: Diagnostic) -> bool:
        return self.name in diagnosis.allowed_repair_tools

    def solve(self, scene: SceneSnapshot, diagnosis: Diagnostic, request: RepairRequest) -> ToolOutcome:
        raise NotImplementedError

    @staticmethod
    def _target_id(request: RepairRequest, *, movable: bool = False) -> str:
        target = request.movable_object_id if movable else request.object_id
        if not target:
            raise DeterministicToolError("missing_target_object")
        return target

    @staticmethod
    def _object(scene: SceneSnapshot, object_id: str):
        obj = next((item for item in scene.objects if item.object_id == object_id), None)
        if obj is None:
            raise DeterministicToolError("unknown_object")
        if not obj.movable:
            raise DeterministicToolError("fixed_object")
        return obj

    @staticmethod
    def _apply_pose(scene: SceneSnapshot, object_id: str, pose):
        objects = []
        found = False
        for obj in scene.objects:
            if obj.object_id == object_id:
                found = True
                if not obj.movable:
                    raise DeterministicToolError("fixed_object")
                obj = obj.model_copy(update={"geometry": obj.geometry.model_copy(update={"pose": pose})})
            objects.append(obj)
        if not found:
            raise DeterministicToolError("unknown_object")
        return SceneSnapshot.model_validate(scene.model_copy(update={"objects": tuple(objects)}).model_dump(mode="json"))

    def _outcome(
        self,
        scene: SceneSnapshot,
        diagnosis: Diagnostic,
        object_id: str,
        after_pose,
        *,
        delta_m=None,
        new_orientation_xyzw=None,
        reason_code: str,
    ) -> ToolOutcome:
        before_pose = self._object(scene, object_id).geometry.pose
        candidate = self._apply_pose(scene, object_id, after_pose)
        identity = {
            "tool_name": self.name,
            "diagnosis_id": diagnosis.diagnosis_id,
            "object_id": object_id,
            "scene_revision_before": scene.revision,
            "scene_revision_after": candidate.revision,
            "after_pose": after_pose.model_dump(mode="json"),
        }
        action_id = "repair:" + sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        action = RepairAction(
            repair_action_id=action_id,
            diagnosis_id=diagnosis.diagnosis_id,
            tool_name=self.name,
            object_id=object_id,
            before_pose=before_pose,
            after_pose=after_pose,
            delta_m=delta_m,
            new_orientation_xyzw=new_orientation_xyzw,
            scene_revision_before=scene.revision,
            scene_revision_after=candidate.revision,
            reason_code=reason_code,
        )
        return ToolOutcome(scene=candidate, action=action)

    @staticmethod
    def _check_revision(scene: SceneSnapshot, request: RepairRequest):
        if request.scene_revision != scene.revision:
            raise DeterministicToolError("stale_scene_revision")

    @staticmethod
    def _check_diagnosis(diagnosis: Diagnostic, request: RepairRequest):
        if diagnosis.diagnosis_id != request.diagnosis_id:
            raise DeterministicToolError("diagnosis_mismatch")
        if diagnosis.status != "fail":
            raise DeterministicToolError("diagnosis_not_failed")

    @staticmethod
    def _bounded(delta, maximum: float):
        if sqrt(sum(value * value for value in delta)) > maximum + 1e-12:
            raise DeterministicToolError("movement_budget_exceeded")


class ResolveCollisionTool(DeterministicRepairTool):
    name = "resolve_collision"

    def __init__(self, config: ToolConfig):
        self.config = config

    def can_handle(self, diagnosis: Diagnostic) -> bool:
        return diagnosis.rule_id in {"collision", "penetration"} and super().can_handle(diagnosis)

    def solve(self, scene, diagnosis, request):
        self._check_revision(scene, request)
        self._check_diagnosis(diagnosis, request)
        object_id = self._target_id(request, movable=True)
        if object_id not in diagnosis.editable_objects:
            raise DeterministicToolError("object_not_editable_for_diagnosis")
        target = self._object(scene, object_id)
        other_ids = [item for item in diagnosis.object_ids if item != object_id and item != "floor"]
        if len(other_ids) != 1:
            raise DeterministicToolError("collision_requires_one_other_object")
        other = next((item for item in scene.objects if item.object_id == other_ids[0]), None)
        if other is None:
            raise DeterministicToolError("unknown_collision_object")
        solutions = penetration(target.geometry, other.geometry, self.config.penetration_tolerance_m)
        if not solutions:
            raise DeterministicToolError("collision_already_resolved")
        _, delta = solutions[0]
        self._bounded(delta, self.config.maximum_move_m)
        after = target.geometry.pose.model_copy(
            update={"position_m": tuple(a + b for a, b in zip(target.geometry.pose.position_m, delta))}
        )
        return self._outcome(
            scene,
            diagnosis,
            object_id,
            after,
            delta_m=delta,
            reason_code="MINIMUM_SEPARATION_VECTOR",
        )


class RepairSupportContactTool(DeterministicRepairTool):
    name = "repair_support_contact"

    def __init__(self, config: ToolConfig):
        self.config = config

    def can_handle(self, diagnosis: Diagnostic) -> bool:
        return diagnosis.rule_id in {"support", "floating", "support_gap"} and super().can_handle(diagnosis)

    def solve(self, scene, diagnosis, request):
        self._check_revision(scene, request)
        self._check_diagnosis(diagnosis, request)
        object_id = self._target_id(request)
        if object_id not in diagnosis.editable_objects:
            raise DeterministicToolError("object_not_editable_for_diagnosis")
        target = self._object(scene, object_id)
        if not aligned(target.geometry, 1e-9):
            raise DeterministicToolError("rotated_object_not_supported_in_v0")
        low, _ = bounds(target.geometry)
        support_id = target.support_id
        if support_id is None:
            raise DeterministicToolError("support_intent_missing")
        if support_id == "floor":
            support_top = scene.floor_z_m
        else:
            support = next((item for item in scene.objects if item.object_id == support_id), None)
            if support is None:
                raise DeterministicToolError("declared_support_missing")
            if not aligned(support.geometry, 1e-9):
                raise DeterministicToolError("rotated_support_not_supported_in_v0")
            _, support_high = bounds(support.geometry)
            support_top = support_high[2]
        gap = low[2] - support_top
        if abs(gap) <= self.config.contact_tolerance_m:
            raise DeterministicToolError("support_contact_already_satisfied")
        delta = (0.0, 0.0, -gap)
        self._bounded(delta, self.config.maximum_move_m)
        after = target.geometry.pose.model_copy(
            update={"position_m": tuple(a + b for a, b in zip(target.geometry.pose.position_m, delta))}
        )
        return self._outcome(
            scene,
            diagnosis,
            object_id,
            after,
            delta_m=delta,
            reason_code="SNAP_TO_SUPPORT",
        )


class StabilizeSupportTool(DeterministicRepairTool):
    name = "stabilize_support"

    def __init__(self, config: ToolConfig):
        self.config = config

    def can_handle(self, diagnosis: Diagnostic) -> bool:
        return diagnosis.rule_id in {"support", "support_chain", "support_instability"} and super().can_handle(diagnosis)

    def solve(self, scene, diagnosis, request):
        self._check_revision(scene, request)
        self._check_diagnosis(diagnosis, request)
        object_id = self._target_id(request)
        if object_id not in diagnosis.editable_objects:
            raise DeterministicToolError("object_not_editable_for_diagnosis")
        target = self._object(scene, object_id)
        support_id = target.support_id
        if support_id in (None, "floor"):
            raise DeterministicToolError("finite_support_surface_required")
        support = next((item for item in scene.objects if item.object_id == support_id), None)
        if support is None:
            raise DeterministicToolError("declared_support_missing")
        if not aligned(target.geometry, 1e-9) or not aligned(support.geometry, 1e-9):
            raise DeterministicToolError("rotated_support_not_supported_in_v0")
        target_low, target_high = bounds(target.geometry)
        support_low, support_high = bounds(support.geometry)
        com_offset = rotate(target.center_of_mass_local_m or (0.0, 0.0, 0.0), target.geometry.pose)
        com = tuple(a + b for a, b in zip(target.geometry.pose.position_m, com_offset))
        gap = target_low[2] - support_high[2]
        if abs(gap) > self.config.contact_tolerance_m:
            raise DeterministicToolError("support_height_must_be_repaired_first")

        strategy = request.strategy or "nearest_valid"
        minimum = self.config.minimum_support_margin_m
        if strategy in {"center_on_support", "maximize_support_margin"}:
            target_com = tuple((support_low[i] + support_high[i]) / 2 for i in range(2))
        else:
            target_com = []
            for i in range(2):
                lo = support_low[i] + minimum
                hi = support_high[i] - minimum
                if lo > hi:
                    target_com.append((support_low[i] + support_high[i]) / 2)
                else:
                    target_com.append(min(max(com[i], lo), hi))
            target_com = tuple(target_com)
        delta = (target_com[0] - com[0], target_com[1] - com[1], 0.0)
        self._bounded(delta, self.config.maximum_move_m)
        if sqrt(sum(value * value for value in delta)) <= 1e-12:
            raise DeterministicToolError("support_is_already_stable")
        after = target.geometry.pose.model_copy(
            update={"position_m": tuple(a + b for a, b in zip(target.geometry.pose.position_m, delta))}
        )
        return self._outcome(
            scene,
            diagnosis,
            object_id,
            after,
            delta_m=delta,
            reason_code="LEGAL_SUPPORT_REGION",
        )


class RepairBoundaryTool(DeterministicRepairTool):
    name = "repair_boundary"

    def __init__(self, config: ToolConfig):
        self.config = config

    def can_handle(self, diagnosis: Diagnostic) -> bool:
        return diagnosis.rule_id in {"floor_penetration", "out_of_room", "wall_penetration"} and super().can_handle(diagnosis)

    def solve(self, scene, diagnosis, request):
        self._check_revision(scene, request)
        self._check_diagnosis(diagnosis, request)
        object_id = self._target_id(request)
        if object_id not in diagnosis.editable_objects:
            raise DeterministicToolError("object_not_editable_for_diagnosis")
        target = self._object(scene, object_id)
        if diagnosis.rule_id != "floor_penetration":
            raise DeterministicToolError("room_boundary_not_available_in_box_v0")
        low, _ = bounds(target.geometry)
        penetration_depth = scene.floor_z_m - low[2]
        if penetration_depth <= self.config.penetration_tolerance_m:
            raise DeterministicToolError("boundary_already_satisfied")
        delta = (0.0, 0.0, penetration_depth)
        self._bounded(delta, self.config.maximum_move_m)
        after = target.geometry.pose.model_copy(
            update={"position_m": tuple(a + b for a, b in zip(target.geometry.pose.position_m, delta))}
        )
        return self._outcome(
            scene,
            diagnosis,
            object_id,
            after,
            delta_m=delta,
            reason_code="PROJECT_TO_FLOOR_DOMAIN",
        )


class UnsupportedV0Tool(DeterministicRepairTool):
    """Catalogued tools whose solver data is not present in the box V0 scene."""

    def __init__(self, name: str, rule_ids: set[str]):
        self.name = name
        self.rule_ids = rule_ids

    def can_handle(self, diagnosis: Diagnostic) -> bool:
        return diagnosis.rule_id in self.rule_ids and super().can_handle(diagnosis)

    def solve(self, scene, diagnosis, request):
        raise DeterministicToolError(f"{self.name}_requires_v1_solver_data")
