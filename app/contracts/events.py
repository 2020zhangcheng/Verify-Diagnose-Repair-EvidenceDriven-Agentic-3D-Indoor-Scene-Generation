"""Typed durable event envelope; persistence is intentionally not implemented."""
from typing import Annotated, Literal, Union
from pydantic import AwareDatetime, Field, JsonValue
from .models import Contract, ID, Observation, SceneBelief, CandidateLayout, CriticalUnknown, CandidateView, VerificationResult, ActionResult, Decision, TaskSpec


class UserMessagePayload(Contract):
    kind: Literal["USER_MESSAGE"]
    message: str


class EntityPayload(Contract):
    kind: Literal["ENTITY_RECORDED"]
    entity: Union[Observation, SceneBelief, CandidateLayout, CriticalUnknown,
                  CandidateView, VerificationResult, ActionResult, Decision, TaskSpec]


class ToolCallPayload(Contract):
    kind: Literal["TOOL_CALL"]
    operation_id: ID
    tool_name: ID
    arguments: dict[str, JsonValue]


class ToolResultPayload(Contract):
    kind: Literal["TOOL_RESULT"]
    operation_id: ID
    status: Literal["succeeded", "failed", "unknown"]
    result_entity_ids: tuple[ID, ...] = ()
    error_code: str | None = None


class LifecyclePayload(Contract):
    kind: Literal["TASK_CREATED", "RUN_REQUESTED", "TASK_FINISHED", "TASK_BLOCKED",
                  "TASK_FAILED", "VERIFICATION_STARTED", "ACTION_REQUESTED", "CAMERA_MOVED", "NEW_OBSERVATION", "VERIFICATION", "VIEW_CANDIDATES_GENERATED"]
    entity_ids: tuple[ID, ...] = ()
    reason: str | None = None


Payload = Annotated[Union[UserMessagePayload, EntityPayload, ToolCallPayload,
                          ToolResultPayload, LifecyclePayload], Field(discriminator="kind")]


class Event(Contract):
    event_id: ID
    task_id: ID
    project_id: ID
    user_id: ID
    run_id: ID | None = None
    sequence: Annotated[int, Field(ge=1)]
    idempotency_key: ID
    causation_id: ID | None = None
    correlation_id: ID
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    producer: ID
    payload: Payload
