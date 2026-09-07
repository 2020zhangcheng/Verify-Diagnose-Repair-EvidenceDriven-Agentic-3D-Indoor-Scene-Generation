"""Event-first node wrapper with durable result reuse across checkpoint gaps."""
from sqlalchemy.orm import Session
from pydantic import TypeAdapter
from langgraph.graph import StateGraph, START, END
from app.agent.state import RoomScoutState
from app.agent.edges import need_more_evidence, verified
from app.agent.nodes.fake import FakeNodes
from app.contracts.models import Budget
from app.db.models.tables import Operation
from app.db.repositories.store import locked_task, append_event, save_entity, lifecycle, digest, entity_id

NODES = ("understand_task", "recall_memory", "observe_scene", "update_scene_belief",
         "generate_candidate_layouts", "analyze_critical_unknowns", "generate_candidate_views",
         "select_best_view", "verify_layout", "execute", "validate", "blocked")


def initial_state(task, user_id="demo-user"):
    return dict(schema_version=1, task_id=task.id, user_id=user_id, project_id=task.project_id,
        run_id=task.run_id, user_request=task.request, task_spec_id=None, observation_ids=[], scene_belief=None,
        candidate_layout_ids=[], critical_unknown_ids=[], candidate_view_ids=[], selected_view_id=None,
        verification_result_ids=[], action_result_id=None, recalled_memory_ids=[], stage="created",
        decision=None, budget=Budget(remaining_views=1, remaining_travel_m=5).model_dump(mode="json"),
        last_event_sequence=task.sequence, pending_operation_id=None, config_id=task.config_id, seed=task.seed,
        observation_round=0, final_result=None)


class NodeRuntime:
    def __init__(self, connection, crash_after=None):
        self.connection = connection
        self.crash_after = crash_after

    def node(self, name):
        def run(state):
            # A node's key is stable when replaying its original checkpoint input.
            op_id = f"{state['run_id']}:{name}:{state['observation_round']}"
            request_hash = digest(state)
            with Session(self.connection) as session, session.begin():
                task = locked_task(session, state["task_id"])
                op = session.get(Operation, op_id)
                if op:
                    if op.request_hash != request_hash:
                        raise ValueError("operation input changed on replay")
                    if op.status == "completed":
                        return op.patch
                else:
                    op = Operation(id=op_id, task_id=task.id, node=name, request_hash=request_hash)
                    session.add(op)
                    tool_name = f"fake.{name}"
                    arguments = {"state_hash": request_hash, "mode": "fake-v0"}
                    if name == "update_scene_belief" and state["config_id"] == "scene-belief-v1":
                        from app.scene.node import describe_call
                        description = describe_call(session, state)
                        tool_name = description.pop("tool_name")
                        arguments.update(description, mode="structured-fusion")
                    append_event(session, task, "TOOL_CALL", {"kind": "TOOL_CALL", "operation_id": op_id,
                                 "tool_name": tool_name, "arguments": arguments}, f"{op_id}:call")
                    if name == "verify_layout":
                        append_event(session, task, "VERIFICATION_STARTED", lifecycle("VERIFICATION_STARTED", state["candidate_layout_ids"]), f"{op_id}:start")
                    if name == "execute":
                        append_event(session, task, "ACTION_REQUESTED", lifecycle("ACTION_REQUESTED", (state["decision"]["decision_id"],)), f"{op_id}:start")
            # Intent is committed. Fake has no external side effects and can be deterministically replayed.
            with Session(self.connection) as session:
                patch, entities = getattr(FakeNodes(session), name)(state, op_id)
            with Session(self.connection) as session, session.begin():
                task = locked_task(session, state["task_id"])
                if name == "observe_scene" and state["selected_view_id"]:
                    append_event(session, task, "CAMERA_MOVED", lifecycle("CAMERA_MOVED", (entities[0][1].camera_view_id, state["selected_view_id"]), "fake-v0"), f"{op_id}:camera")
                for i, (event_type, entity) in enumerate(entities):
                    save_entity(session, task, entity, event_type, f"{op_id}:entity:{i}")
                if name == "observe_scene" and state["observation_round"] > 0:
                    append_event(session, task, "NEW_OBSERVATION", lifecycle("NEW_OBSERVATION", patch["observation_ids"][-1:]), f"{op_id}:new")
                if name == "verify_layout":
                    append_event(session, task, "VERIFICATION", lifecycle("VERIFICATION", patch["verification_result_ids"], "fake-v0"), f"{op_id}:summary")
                append_event(session, task, "TOOL_RESULT", {"kind": "TOOL_RESULT", "operation_id": op_id,
                             "status": "succeeded", "result_entity_ids": [entity_id(e) for _, e in entities]}, f"{op_id}:result")
                if name in ("validate", "blocked"):
                    kind = "TASK_FINISHED" if name == "validate" else "TASK_BLOCKED"
                    append_event(session, task, kind, lifecycle(kind, (task.id,), "fake-v0"), f"{op_id}:terminal")
                patch["last_event_sequence"] = task.sequence
                task.stage = patch["stage"]
                task.state = TypeAdapter(RoomScoutState).validate_python({**state, **patch})
                # Finished status is set by runner only AFTER the final checkpoint commit.
                op = session.get(Operation, op_id)
                op.status = "completed"
                op.patch = patch
            if self.crash_after == f"hard:{name}":
                import os
                os._exit(86)  # Test-only process death at the commit/checkpoint gap.
            if self.crash_after == name:
                raise SimulatedCrash(f"after {name} result commit, before checkpoint")
            return patch
        return run


class SimulatedCrash(RuntimeError):
    pass


def build_graph(checkpointer, connection, *, interrupt_before=None, crash_after=None):
    runtime = NodeRuntime(connection, crash_after)
    graph = StateGraph(RoomScoutState)
    for name in NODES:
        graph.add_node(name, runtime.node(name))
    graph.add_edge(START, "understand_task")
    for a, b in zip(NODES[:5], NODES[1:6]):
        graph.add_edge(a, b)
    graph.add_conditional_edges("analyze_critical_unknowns", need_more_evidence,
                               {n: n for n in ("generate_candidate_views", "verify_layout", "blocked")})
    graph.add_edge("generate_candidate_views", "select_best_view")
    graph.add_edge("select_best_view", "observe_scene")
    graph.add_conditional_edges("verify_layout", verified, {"execute": "execute", "blocked": "blocked"})
    graph.add_edge("execute", "validate")
    graph.add_edge("validate", END)
    graph.add_edge("blocked", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)
