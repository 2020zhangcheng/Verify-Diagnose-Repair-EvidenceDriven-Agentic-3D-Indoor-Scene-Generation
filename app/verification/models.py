"""Contracts emitted by the deterministic Geometry Critic."""

from __future__ import annotations

from hashlib import sha256
import json
from math import sqrt
from typing import Literal

from pydantic import Field, model_validator

from app.contracts.models import Box, Contract, ID, NonNegative, Positive, Vec3


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value) -> str:
    return sha256(canonical(value.model_dump(mode="json"))).hexdigest()


class GeometryObject(Contract):
    object_id: ID
    geometry: Box
    movable: bool = True
    requires_support: bool = True
    anchored: bool = False
    support_id: ID | None = None
    center_of_mass_local_m: Vec3 | None = None
    affordance: "Affordance | None" = None


class Affordance(Contract):
    """Deterministic description of how an object may be interacted with.

    The geometry critic does not require an affordance.  When one is present,
    the optional Functional Critic can reason about interaction distance,
    approach direction, clearance and an operation sweep without asking the
    LLM to invent coordinates.
    """

    type: ID
    interaction_anchor_local_m: Vec3 = (0.0, 0.0, 0.0)
    front_axis_local: Vec3 = (0.0, -1.0, 0.0)
    approach_distance_m: tuple[float, float] = (0.25, 0.80)
    approach_angle_deg: float = Field(default=35.0, ge=0.0, le=180.0)
    clearance_size_m: tuple[Positive, Positive, Positive] | None = None
    operation_sweep_size_m: tuple[Positive, Positive, Positive] | None = None
    reach_radius_m: Positive | None = None

    @model_validator(mode="after")
    def valid(self):
        if sqrt(sum(value * value for value in self.front_axis_local)) <= 1e-12:
            raise ValueError("front_axis_local must be non-zero")
        lower, upper = self.approach_distance_m
        if lower < 0 or upper < 0 or lower > upper:
            raise ValueError("approach_distance_m must be a non-negative interval")
        return self


class FunctionalContext(Contract):
    """Optional room and human-agent parameters for Functional Critic V1."""

    room_bounds_xy_m: tuple[float, float, float, float] | None = None
    agent_start_position_m: tuple[float, float] | None = None
    agent_radius_m: Positive = 0.30
    navigation_clearance_m: NonNegative = 0.05
    grid_resolution_m: Positive = 0.05
    default_reach_radius_m: Positive = 0.75
    clearance_block_ratio_max: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def valid(self):
        if self.room_bounds_xy_m is not None:
            xmin, ymin, xmax, ymax = self.room_bounds_xy_m
            if xmin >= xmax or ymin >= ymax:
                raise ValueError("room_bounds_xy_m must be min_x, min_y, max_x, max_y")
        if self.agent_start_position_m is not None and self.room_bounds_xy_m is not None:
            xmin, ymin, xmax, ymax = self.room_bounds_xy_m
            x, y = self.agent_start_position_m
            if not xmin <= x <= xmax or not ymin <= y <= ymax:
                raise ValueError("agent_start_position_m must be inside room_bounds_xy_m")
        return self


class FunctionalRelation(Contract):
    """Distance/orientation relation between two functional objects."""

    relation_id: ID
    subject_id: ID
    object_id: ID
    distance_m: tuple[float, float]
    angle_deg: float | None = Field(default=None, ge=0.0, le=180.0)

    @model_validator(mode="after")
    def valid(self):
        lower, upper = self.distance_m
        if lower < 0 or upper < 0 or lower > upper:
            raise ValueError("distance_m must be a non-negative interval")
        return self


class SceneSnapshot(Contract):
    scene_id: ID
    frame_id: ID = "world"
    floor_z_m: float = 0
    objects: tuple[GeometryObject, ...] = Field(min_length=1)
    functional_context: FunctionalContext | None = None
    functional_relations: tuple[FunctionalRelation, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        ids = [obj.object_id for obj in self.objects]
        if len(set(ids)) != len(ids) or "floor" in ids:
            raise ValueError("duplicate or reserved object ID")
        for obj in self.objects:
            if obj.geometry.pose.frame_id != self.frame_id:
                raise ValueError("frame mismatch")
            if obj.support_id == obj.object_id:
                raise ValueError("self support is invalid")
            if obj.anchored and obj.movable:
                raise ValueError("anchored objects cannot be movable")
            if obj.center_of_mass_local_m is not None and any(
                abs(value) > size / 2
                for value, size in zip(obj.center_of_mass_local_m, obj.geometry.size_m)
            ):
                raise ValueError("center of mass must be inside solid box")
        return self

    @property
    def revision(self) -> str:
        return "geometry-v1:" + fingerprint(self)


class VerifierConfig(Contract):
    penetration_tolerance_m: NonNegative
    contact_tolerance_m: NonNegative
    minimum_support_margin_m: NonNegative
    axis_tolerance: Positive
    severity_scale_m: Positive
    maximum_move_m: Positive
    maximum_iterations: int = Field(ge=1, le=100)


class FunctionalVerifierConfig(Contract):
    """Versioned defaults for the optional deterministic Functional Critic."""

    agent_radius_m: Positive
    navigation_clearance_m: NonNegative
    grid_resolution_m: Positive
    default_approach_angle_deg: float = Field(ge=0.0, le=180.0)
    default_reach_radius_m: Positive
    clearance_block_ratio_max: float = Field(ge=0.0, le=1.0)
    distance_severity_scale_m: Positive
    angle_severity_scale_deg: Positive
    maximum_move_m: Positive
    maximum_iterations: int = Field(ge=1, le=100)


RuleId = Literal[
    "collision",
    "penetration",
    "floor_penetration",
    "support",
    "support_chain",
    "floating",
    "support_gap",
    "support_instability",
    "out_of_room",
    "wall_penetration",
    "door_clearance",
    "path_blocked",
    "spacing_too_small",
    "orientation",
    "upright",
    "normal_mismatch",
    "functional_navigation",
    "functional_approach",
    "functional_clearance",
    "operation_sweep_blocked",
    "reach_unavailable",
    "functional_relation",
    "functional_orientation",
]


class MovePrescription(Contract):
    """A deterministic candidate solution emitted by a Diagnosis."""

    object_id: ID
    delta_m: Vec3
    diagnosis_id: ID


class Diagnostic(Contract):
    diagnosis_id: ID
    rule_id: RuleId
    status: Literal["pass", "fail", "unknown"]
    object_ids: tuple[ID, ...]
    reason: str
    measurements: dict[str, float | bool | None]
    severity: float = Field(ge=0, le=1)
    # Kept as an explicit mathematical solution contract.  The LLM does not
    # receive these numeric deltas; deterministic tools recompute them.
    editable_variables: tuple[str, ...] = ()
    suggestions: tuple[MovePrescription, ...] = ()
    editable_objects: tuple[ID, ...] = ()
    locked_objects: tuple[ID, ...] = ()
    allowed_repair_tools: tuple[ID, ...] = ()


class DiagnosisReport(Contract):
    scene_revision: ID
    verifier_version: ID
    status: Literal["pass", "fail", "unknown"]
    diagnostics: tuple[Diagnostic, ...]
    assumptions: tuple[str, ...]
