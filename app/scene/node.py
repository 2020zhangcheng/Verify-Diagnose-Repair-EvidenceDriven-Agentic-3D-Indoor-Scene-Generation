"""Thin application bridge: only persisted observations enter the pure service."""
import asyncio
from sqlalchemy import select
from app.contracts.models import Observation, SceneBelief, TaskSpec
from app.db.models.tables import EventRow
from app.db.repositories.store import load_entity
from app.scene.config import FusionConfig, load_config
from app.scene.belief import StructuredSceneBeliefService


def describe_call(session, state):
    # Pin the first fusion config to the task, including across restart/deployment.
    calls = session.scalars(select(EventRow).where(EventRow.task_id == state["task_id"], EventRow.type == "TOOL_CALL").order_by(EventRow.sequence))
    config = next((FusionConfig.model_validate(row.payload["arguments"]["fusion_config"])
                   for row in calls if row.payload["tool_name"] == "scene.update"), None) or load_config()
    return {"tool_name": "scene.update", "fusion_version": config.version, "fusion_config": config.model_dump(mode="json")}


def update_belief_node(session, state, operation_id):
    call = session.scalar(select(EventRow).where(EventRow.task_id == state["task_id"], EventRow.idempotency_key == f"{operation_id}:call"))
    if call is None or call.payload["tool_name"] != "scene.update":
        raise ValueError("fusion intent must be persisted before processing")
    config = FusionConfig.model_validate(call.payload["arguments"]["fusion_config"])
    task = load_entity(session, state["task_id"], state["task_spec_id"], TaskSpec)
    previous = None
    if state["scene_belief"]:
        ref = state["scene_belief"]
        previous = load_entity(session, state["task_id"], f"{ref['belief_id']}@{ref['version']}", SceneBelief)
    observation = load_entity(session, state["task_id"], state["observation_ids"][-1], Observation)

    async def load_history(task_id, ids):
        return tuple(load_entity(session, task_id, ident, Observation) for ident in ids)

    service = StructuredSceneBeliefService(load_history, config)
    belief = asyncio.run(service.update(previous, (observation,), task))
    return {"scene_belief": belief.ref.model_dump(mode="json"), "candidate_layout_ids": [],
            "critical_unknown_ids": [], "candidate_view_ids": [], "selected_view_id": None,
            "verification_result_ids": [], "decision": None, "stage": "update_belief"}, [("BELIEF_UPDATED", belief)]
