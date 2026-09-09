"""Version 1 domain contracts. No planning, fusion or scoring algorithms."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

ID = Annotated[str, Field(min_length=1)]
Unit = Annotated[float, Field(ge=0, le=1)]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Vec3 = tuple[float, float, float]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1


class BeliefRef(Contract):
    belief_id: ID
    version: Annotated[int, Field(ge=1)]


class CameraPose(Contract):
    frame_id: ID
    position_m: Vec3
    orientation_xyzw: tuple[float, float, float, float]

    @model_validator(mode="after")
    def unit_quaternion(self) -> CameraPose:
        if abs(sum(v * v for v in self.orientation_xyzw) - 1) > 1e-5:
            raise ValueError("orientation must be a unit quaternion (xyzw)")
        return self


class Box(Contract):
    pose: CameraPose
    size_m: tuple[Positive, Positive, Positive]


class ArtifactRef(Contract):
    artifact_id: ID
    uri: ID
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    media_type: ID


class EvidenceRef(Contract):
    observation_id: ID
    camera_view_id: ID
    artifact_id: ID | None = None


class Uncertainty(Contract):
    semantic_confidence: Unit | None = None
    geometry_confidence: Unit | None = None
    geometry_uncertainty: Unit | None = None
    visibility: Unit | None = None
    occlusion_ratio: Unit | None = None
    observation_count: Annotated[int, Field(ge=0)] = 0
    cross_view_inconsistency: Unit | None = None
    aggregate: Unit | None = None
    estimator_version: ID


class SceneObject(Contract):
    object_id: ID
    semantic_class: ID | None = None
    geometry: Box | None = None
    existence_probability: Unit | None = None
    knowledge: Literal["observed", "inferred", "unobserved", "conflicted"]
    uncertainty: Uncertainty
    evidence: tuple[EvidenceRef, ...] = ()


class SceneRegion(Contract):
    region_id: ID
    kind: Literal["free", "occluded", "uncertain", "unobserved", "occupied"]
    bounds: Box
    uncertainty: Uncertainty
    evidence: tuple[EvidenceRef, ...] = ()


class Claim(Contract):
    claim_id: ID
    subject_id: ID
    predicate: ID
    value: bool | float | str | None
    unit: str | None = None
    status: Literal["supported", "uncertain", "unobserved", "conflicted", "refuted"]
    confidence: Unit | None = None
    evidence: tuple[EvidenceRef, ...] = ()


class Observation(Contract):
    observation_id: ID
    task_id: ID
    camera_view_id: ID
    camera_pose: CameraPose
    captured_at: AwareDatetime
    environment_revision: ID
    source: Literal["simulation", "replay", "sensor"]
    artifacts: tuple[ArtifactRef, ...]
    detected_objects: tuple[SceneObject, ...] = ()
    observed_regions: tuple[SceneRegion, ...] = ()
    observed_claims: tuple[Claim, ...] = ()
    observed_room_geometry: RoomGeometry | None = None
    sensor_calibration_id: ID
    operation_id: ID


class RoomGeometry(Contract):
    walls: tuple[Box, ...] = ()
    floors: tuple[Box, ...] = ()
    windows: tuple[Box, ...] = ()
    doors: tuple[Box, ...] = ()
    complete: bool = False
    evidence: tuple[EvidenceRef, ...] = ()


class SceneBelief(Contract):
    ref: BeliefRef
    task_id: ID
    parent: BeliefRef | None = None
    frame_id: ID
    environment_revision: ID
    room_geometry: RoomGeometry
    objects: tuple[SceneObject, ...] = ()
    regions: tuple[SceneRegion, ...] = ()
    claims: tuple[Claim, ...] = ()
    observation_ids: tuple[ID, ...] = ()
    fusion_version: ID


class Constraint(Contract):
    constraint_id: ID
    rule_id: ID
    hard: bool
    object_ids: tuple[ID, ...] = ()
    relation: Literal["eq", "le", "ge", "true"]
    threshold: float | bool
    unit: str | None = None


class TaskSpec(Contract):
    task_id: ID
    objective: str
    constraints: tuple[Constraint, ...]
    soft_preferences: tuple[str, ...] = ()
    seed: int
    config_id: ID


class LayoutAssumption(Contract):
    assumption_id: ID
    claim_id: ID
    expected_value: bool | float | str
    confidence: Unit | None = None
    evidence: tuple[EvidenceRef, ...] = ()


class Placement(Contract):
    object_id: ID
    target: Box


class CandidateLayout(Contract):
    layout_id: ID
    task_id: ID
    belief_ref: BeliefRef
    placements: tuple[Placement, ...]
    constraints: tuple[Constraint, ...]
    required_assumptions: tuple[LayoutAssumption, ...]
    expected_score: float | None = None
    feasibility: Unit | None = None
    planner_version: ID


class OutcomeEffect(Contract):
    layout_id: ID
    feasibility: Literal["feasible", "infeasible", "unknown"]
    score_delta: float | None = None
    rank: Annotated[int, Field(ge=1)] | None = None
    action_changes: bool


class PossibleOutcome(Contract):
    outcome_id: ID
    value: bool | float | str
    probability: Unit | None = None
    effects: tuple[OutcomeEffect, ...] = Field(min_length=1)


class CriticalUnknown(Contract):
    unknown_id: ID
    belief_ref: BeliefRef
    claim_id: ID
    assumption_ids: tuple[ID, ...] = Field(min_length=1)
    affected_layout_ids: tuple[ID, ...] = Field(min_length=1)
    description: str
    possible_outcomes: tuple[PossibleOutcome, ...] = Field(min_length=2)
    decision_impact: Unit
    current_uncertainty: Unit
    assessment_method: Literal["rule", "llm_structured", "counterfactual"]
    assessment_version: ID
    status: Literal["open", "resolved", "superseded"] = "open"


class CandidateView(Contract):
    view_id: ID
    belief_ref: BeliefRef
    camera_pose: CameraPose
    target_unknown_ids: tuple[ID, ...] = Field(min_length=1)
    expected_visibility: Unit | None = None
    expected_information_gain: Unit | None = None
    decision_relevance: Unit | None = None
    movement_cost: NonNegative | None = None
    redundancy: Unit | None = None
    reachable: Literal["yes", "no", "unknown"] = "unknown"
    score: float | None = None
    scoring_config_id: ID | None = None


class VerificationResult(Contract):
    verification_id: ID
    task_id: ID
    layout_id: ID
    belief_ref: BeliefRef
    environment_revision: ID
    rule_id: ID
    rule_version: ID
    status: Literal["pass", "fail", "unknown", "error"]
    measured_value: float | bool | None = None
    threshold: float | bool | None = None
    unit: str | None = None
    involved_objects: tuple[ID, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    reason: str | None = None
    checked_at: AwareDatetime
    geometry_artifact: ArtifactRef | None = None


class Decision(Contract):
    decision_id: ID
    task_id: ID
    belief_ref: BeliefRef
    layout_id: ID | None = None
    kind: Literal["observe", "execute", "replan", "blocked", "finish"]
    unknown_ids: tuple[ID, ...] = ()
    verification_ids: tuple[ID, ...] = ()
    rationale: str


class ActionResult(Contract):
    action_id: ID
    operation_id: ID
    decision_id: ID
    layout_id: ID
    task_id: ID
    status: Literal["succeeded", "failed", "partial", "unknown"]
    before_revision: ID
    after_revision: ID | None = None
    verification_ids: tuple[ID, ...] = Field(min_length=1)
    observation_ids: tuple[ID, ...] = ()
    error_code: str | None = None


class OperationContext(Contract):
    operation_id: ID
    task_id: ID
    run_id: ID
    expected_environment_revision: ID
    fencing_token: Annotated[int, Field(ge=1)]


class ViewSelection(Contract):
    selected_view_id: ID | None
    reason: Literal["selected", "no_reachable_view", "no_decision_gain", "budget_exhausted"]
    scored_views: tuple[CandidateView, ...]


class Budget(Contract):
    remaining_views: Annotated[int, Field(ge=0)]
    remaining_travel_m: NonNegative


class MemoryRecord(Contract):
    memory_id: ID
    kind: Literal["profile", "project", "episodic", "experience"]
    content: str
    source_event_ids: tuple[ID, ...]


class MemoryScope(Contract):
    user_id: ID
    project_id: ID
    agent_id: ID


class GeometryQuery(Contract):
    rule_id: ID
    layout_id: ID
    object_ids: tuple[ID, ...]
    region_ids: tuple[ID, ...] = ()


class GeometryEvidence(Contract):
    environment_revision: ID
    status: Literal["available", "unknown", "unsupported"]
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()


class MoveResult(Contract):
    operation_id: ID
    status: Literal["succeeded", "failed", "unknown"]
    camera_view_id: ID | None = None
    actual_pose: CameraPose | None = None
    environment_revision: ID


class OperationStatus(Contract):
    operation_id: ID
    status: Literal["not_started", "running", "succeeded", "failed", "unknown"]
    result_event_id: ID | None = None

# Resolve the optional room geometry forward reference after all domain types exist.
Observation.model_rebuild()
