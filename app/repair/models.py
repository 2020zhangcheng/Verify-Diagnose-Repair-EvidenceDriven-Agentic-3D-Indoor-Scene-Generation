"""Contracts shared by deterministic repair tools and the LangGraph state."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from app.contracts.models import Contract, ID, Vec3, CameraPose
from app.verification.models import DiagnosisReport, SceneSnapshot


class RepairRequest(Contract):
    """Validated request passed from the router to a deterministic tool."""

    diagnosis_id: ID
    scene_revision: ID
    object_id: ID | None = None
    movable_object_id: ID | None = None
    strategy: str | None = None


class RepairAction(Contract):
    """Auditable before/after transform produced by a deterministic tool."""

    repair_action_id: ID
    diagnosis_id: ID
    tool_name: ID
    object_id: ID
    before_pose: CameraPose
    after_pose: CameraPose
    delta_m: Vec3 | None = None
    new_orientation_xyzw: tuple[float, float, float, float] | None = None
    scene_revision_before: ID
    scene_revision_after: ID
    reason_code: ID | None = None


class RepairToolSelection(Contract):
    """The only decision the LLM is allowed to make."""

    selected_diagnosis_id: ID
    tool: ID
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason_code: ID


class RepairToolOutcome(Contract):
    status: Literal["succeeded", "rejected"]
    scene: SceneSnapshot
    action: RepairAction | None = None
    message: str | None = None


class RepairTool(Protocol):
    """Replaceable tool interface from the design document."""

    name: str

    def can_handle(self, diagnosis) -> bool: ...

    def solve(self, scene: SceneSnapshot, diagnosis, request: RepairRequest): ...


class RepairGraphResult(Contract):
    """Serializable result of the Geometry Critic -> Router -> Tool loop."""

    status: Literal["pass", "blocked", "iteration_limit", "error"]
    initial_revision: ID
    scene: SceneSnapshot
    report: DiagnosisReport
    actions: tuple[RepairAction, ...] = ()
    react_trace: tuple[dict[str, Any], ...] = ()
    iterations: int = Field(ge=0)
    error: str | None = None
