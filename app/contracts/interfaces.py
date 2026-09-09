"""Replaceable async ports. Implementations must not bypass Event First orchestration."""
from typing import Protocol, TypeVar
from .models import (
    ActionResult, ArtifactRef, Budget, CameraPose, CandidateLayout, CandidateView,
    Contract, CriticalUnknown, Decision, GeometryEvidence, GeometryQuery,
    MemoryRecord, MemoryScope, MoveResult, Observation, OperationContext,
    OperationStatus, SceneBelief, TaskSpec, VerificationResult, ViewSelection,
)

T = TypeVar("T", bound=Contract)


class EnvironmentAdapter(Protocol):
    async def observe(self, ctx: OperationContext) -> Observation: ...
    async def move_camera(self, pose: CameraPose, ctx: OperationContext) -> MoveResult: ...
    async def get_depth(self, observation: Observation) -> ArtifactRef | None: ...
    async def measure_geometry(self, query: GeometryQuery, ctx: OperationContext) -> GeometryEvidence: ...
    async def detect_collision(self, layout: CandidateLayout, ctx: OperationContext) -> GeometryEvidence: ...
    async def execute_layout(self, layout: CandidateLayout, decision: Decision,
                             verification: tuple[VerificationResult, ...],
                             ctx: OperationContext) -> ActionResult: ...
    async def reconcile(self, ctx: OperationContext) -> OperationStatus: ...


class SceneBeliefService(Protocol):
    async def update(self, previous: SceneBelief | None,
                     observations: tuple[Observation, ...], task: TaskSpec) -> SceneBelief: ...


class LayoutPlanner(Protocol):
    async def generate(self, task: TaskSpec, belief: SceneBelief,
                       target_count: int = 3) -> tuple[CandidateLayout, ...]: ...


class CriticalUnknownDetector(Protocol):
    async def detect(self, task: TaskSpec, belief: SceneBelief,
                     layouts: tuple[CandidateLayout, ...]) -> tuple[CriticalUnknown, ...]: ...


class ViewGenerator(Protocol):
    async def generate(self, task: TaskSpec, belief: SceneBelief,
                       unknowns: tuple[CriticalUnknown, ...],
                       current_pose: CameraPose) -> tuple[CandidateView, ...]: ...


class ViewSelector(Protocol):
    async def select(self, task: TaskSpec, belief: SceneBelief,
                     layouts: tuple[CandidateLayout, ...],
                     unknowns: tuple[CriticalUnknown, ...],
                     views: tuple[CandidateView, ...],
                     budget: Budget) -> ViewSelection: ...


class GeometryVerifier(Protocol):
    async def verify(self, task: TaskSpec, belief: SceneBelief,
                     layout: CandidateLayout,
                     geometry: GeometryEvidence) -> tuple[VerificationResult, ...]: ...


class MemoryService(Protocol):
    async def recall(self, scope: MemoryScope, query: str,
                     limit: int = 10) -> tuple[MemoryRecord, ...]: ...
    async def ingest(self, scope: MemoryScope, event_ids: tuple[str, ...],
                     idempotency_key: str) -> tuple[MemoryRecord, ...]: ...


class LLMAdapter(Protocol):
    async def reason(self, task: TaskSpec, context: tuple[Contract, ...],
                     output_type: type[T], operation_id: str) -> T: ...


class VLMAdapter(Protocol):
    async def interpret(self, observation: Observation, output_type: type[T],
                        operation_id: str) -> T: ...
