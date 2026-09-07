"""Checkpoint state: versioned references, never sensor bytes or chat-as-belief."""
from typing import Literal
from typing_extensions import NotRequired, TypedDict


class BeliefRefData(TypedDict):
    schema_version: Literal[1]
    belief_id: str
    version: int


class BudgetData(TypedDict):
    schema_version: Literal[1]
    remaining_views: int
    remaining_travel_m: float


class DecisionData(TypedDict):
    schema_version: Literal[1]
    decision_id: str
    task_id: str
    belief_ref: BeliefRefData
    layout_id: str | None
    kind: Literal["observe", "execute", "replan", "blocked", "finish"]
    unknown_ids: list[str]
    verification_ids: list[str]
    rationale: str

Stage = Literal[
    "created", "understand_task", "recall_memory", "observe_scene", "update_belief",
    "generate_layouts", "find_unknowns", "generate_views", "select_view",
    "move_and_observe", "verify", "execute", "validate", "finished", "blocked", "failed",
]


class RoomScoutState(TypedDict):
    schema_version: Literal[1]
    task_id: str
    user_id: str
    project_id: str
    run_id: str
    user_request: str
    task_spec_id: str | None
    observation_ids: list[str]
    scene_belief: BeliefRefData | None
    candidate_layout_ids: list[str]
    critical_unknown_ids: list[str]
    candidate_view_ids: list[str]
    selected_view_id: str | None
    verification_result_ids: list[str]
    action_result_id: str | None
    recalled_memory_ids: list[str]
    stage: Stage
    decision: DecisionData | None
    budget: BudgetData
    last_event_sequence: int
    pending_operation_id: str | None
    config_id: str
    seed: int

    observation_round: NotRequired[int]
    final_result: NotRequired[dict | None]
