"""Explicit ReAct orchestration for the Geometry Critic repair loop.

The loop is deliberately implemented with ordinary Python control flow. Each
stage is a small state transition, and the transition after verification is
explicitly one of ``finish``, ``critic``, ``rollback``, ``iteration_limit`` or
``blocked``. The LLM remains a planner at the routing boundary; deterministic
tools own all geometry edits.
"""

from __future__ import annotations

from typing import Any, Callable, NotRequired, TypedDict

from app.repair.models import RepairAction, RepairResult, RepairToolSelection
from app.repair.registry import RepairToolRegistry, default_repair_tool_registry
from app.repair.router import GeometryRepairRouter
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.llm import LLMRepairError
from app.verification.models import DiagnosisReport, SceneSnapshot


REACT_MAX_ITERATIONS = 10


class RepairLoopState(TypedDict):
    """Mutable-in-one-run state carried by the explicit ReAct loop."""

    scene: SceneSnapshot
    report: DiagnosisReport | None
    verification_report: DiagnosisReport | None
    diagnostics: list[dict[str, Any]]
    selection: RepairToolSelection | None
    tool_result: dict[str, Any] | None
    scene_before_repair: SceneSnapshot | None
    report_before_repair: DiagnosisReport | None
    action: RepairAction | None
    actions: list[RepairAction]
    history: list[dict[str, Any]]
    react_trace: list[dict[str, Any]]
    events: list[dict[str, Any]]
    iteration: int
    max_iterations: int
    status: str
    error: str | None
    initial_revision: str
    last_tool_error: NotRequired[str | None]


def _objective(report: DiagnosisReport) -> tuple[int, int, float]:
    return (
        sum(diagnostic.status == "fail" for diagnostic in report.diagnostics),
        sum(diagnostic.status == "unknown" for diagnostic in report.diagnostics),
        sum(diagnostic.severity for diagnostic in report.diagnostics if diagnostic.status == "fail"),
    )


def _bad_ids(report: DiagnosisReport) -> set[str]:
    return {diagnostic.diagnosis_id for diagnostic in report.diagnostics if diagnostic.status != "pass"}


def _record(
    state: RepairLoopState,
    kind: str,
    payload: dict[str, Any],
    emit: Callable[[str, dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """Persist an event before returning the corresponding state transition."""

    emit(kind, payload)
    return [*state.get("events", []), {"type": kind, "payload": payload}]


def _initial_state(scene: SceneSnapshot, max_iterations: int) -> RepairLoopState:
    return {
        "scene": scene,
        "report": None,
        "verification_report": None,
        "diagnostics": [],
        "selection": None,
        "tool_result": None,
        "scene_before_repair": None,
        "report_before_repair": None,
        "action": None,
        "actions": [],
        "history": [],
        "react_trace": [],
        "events": [],
        "iteration": 0,
        "max_iterations": max_iterations,
        "status": "created",
        "error": None,
        "initial_revision": scene.revision,
    }


def _geometry_critic(
    state: RepairLoopState,
    verifier: DeterministicGeometryVerifier,
    emit: Callable[[str, dict[str, Any]], None],
) -> None:
    report = verifier.diagnose(state["scene"])
    payload = report.model_dump(mode="json")
    events = _record(state, "DIAGNOSIS", payload, emit)
    state.update(
        {
            "report": report,
            "diagnostics": [item.model_dump(mode="json") for item in report.diagnostics],
            "status": report.status,
            "error": None,
            "max_iterations": min(state["max_iterations"], REACT_MAX_ITERATIONS),
            "events": events,
        }
    )


def _route_tool(
    state: RepairLoopState,
    router_holder: dict[str, GeometryRepairRouter | None],
    registry: RepairToolRegistry,
    emit: Callable[[str, dict[str, Any]], None],
    user_message: str | None = None,
) -> str:
    """Ask the LLM to choose one allow-listed tool for the current diagnosis."""

    report = state["report"]
    if report is None:
        state["status"] = "blocked"
        state["error"] = "missing_diagnosis"
        state["events"] = _record(state, "REPAIR_REJECTED", {"reason": "missing_diagnosis"}, emit)
        return "blocked"
    if state["iteration"] >= state["max_iterations"]:
        state["status"] = "iteration_limit"
        return "iteration_limit"

    failed = [item for item in report.diagnostics if item.status == "fail" and item.allowed_repair_tools]
    if not failed:
        payload = {"reason": "no_allowed_repair_tool", "scene_revision": state["scene"].revision}
        state["status"] = "blocked"
        state["error"] = "no_allowed_repair_tool"
        state["events"] = _record(state, "REPAIR_REJECTED", payload, emit)
        return "blocked"

    if router_holder["value"] is None:
        router_holder["value"] = GeometryRepairRouter(registry=registry, emit=emit)
    try:
        selection = router_holder["value"].route(
            state["scene"],
            report,
            state.get("history", []),
            user_message=user_message,
        )
    except LLMRepairError:
        raise
    except Exception as exc:
        payload = {"reason": str(exc), "stage": "llm_repair_router"}
        state.update({"status": "blocked", "error": str(exc)})
        state["events"] = _record(state, "REPAIR_REJECTED", payload, emit)
        return "blocked"

    failed_ids = [item.diagnosis_id for item in report.diagnostics if item.status == "fail"]
    reason_payload = {
        "iteration": state["iteration"] + 1,
        "scene_revision": state["scene"].revision,
        "failed_diagnosis_ids": failed_ids,
        "selection": selection.model_dump(mode="json"),
    }
    events = _record(state, "TOOL_SELECTED", selection.model_dump(mode="json"), emit)
    events = _record({**state, "events": events}, "REACT_REASON", reason_payload, emit)
    state.update(
        {
            "selection": selection,
            "status": "routing",
            "react_trace": [*state.get("react_trace", []), {"phase": "reason", **reason_payload}],
            "events": events,
        }
    )
    return "execute"


def _execute_tool(
    state: RepairLoopState,
    registry: RepairToolRegistry,
    emit: Callable[[str, dict[str, Any]], None],
) -> str:
    report = state["report"]
    selection = state["selection"]
    if report is None or selection is None:
        state.update({"status": "blocked", "error": "missing_tool_selection"})
        return "blocked"

    proposed = {
        "scene_revision": state["scene"].revision,
        "selection": selection.model_dump(mode="json"),
    }
    try:
        outcome = registry.execute(state["scene"], report, selection)
    except Exception as exc:
        payload = {**proposed, "reason": str(exc)}
        state.update({"status": "blocked", "error": str(exc), "last_tool_error": str(exc)})
        state["events"] = _record(state, "REPAIR_REJECTED", payload, emit)
        return "blocked"

    action = outcome.action
    intent_payload = {
        "source_revision": state["scene"].revision,
        "target_revision": outcome.scene.revision,
        "repair_action": action.model_dump(mode="json"),
        "selection": selection.model_dump(mode="json"),
    }
    events = _record(state, "ACTION_INTENT", intent_payload, emit)
    events = _record(
        {**state, "events": events},
        "ACTION_EXECUTED",
        {"scene": outcome.scene.model_dump(mode="json"), "repair_action": action.model_dump(mode="json")},
        emit,
    )
    action_event = {
        "iteration": state["iteration"] + 1,
        "tool": selection.tool,
        "repair_action": action.model_dump(mode="json"),
    }
    events = _record({**state, "events": events}, "REACT_ACTION", action_event, emit)
    history = [
        *state.get("history", []),
        {
            "iteration": state["iteration"] + 1,
            "tool": selection.tool,
            "diagnosis_id": selection.selected_diagnosis_id,
            "arguments": selection.arguments,
            "repair_action_id": action.repair_action_id,
            "status": "applied",
        },
    ]
    state.update(
        {
            "scene": outcome.scene,
            "scene_before_repair": state["scene"],
            "report_before_repair": report,
            "action": action,
            "actions": [*state.get("actions", []), action],
            "history": history,
            "react_trace": [*state.get("react_trace", []), {"phase": "action", **action_event}],
            "tool_result": {
                "status": "succeeded",
                "scene_revision": outcome.scene.revision,
                "repair_action": action.model_dump(mode="json"),
            },
            "events": events,
            "iteration": state["iteration"] + 1,
            "status": "executed",
            "error": None,
        }
    )
    return "verify"


def _verify_scene(
    state: RepairLoopState,
    verifier: DeterministicGeometryVerifier,
    emit: Callable[[str, dict[str, Any]], None],
) -> str:
    report = verifier.diagnose(state["scene"])
    payload = {
        "scene_revision": state["scene"].revision,
        "report": report.model_dump(mode="json"),
        "repair_action_id": state["action"].repair_action_id if state.get("action") else None,
    }
    events = _record(state, "REVERIFICATION", payload["report"], emit)
    events = _record({**state, "events": events}, "TOOL_RESULT", payload, emit)
    observation_event = {
        "iteration": state["iteration"],
        "scene_revision": state["scene"].revision,
        "status": report.status,
        "report": report.model_dump(mode="json"),
    }
    events = _record({**state, "events": events}, "REACT_OBSERVATION", observation_event, emit)
    state.update(
        {
            "report": report,
            "verification_report": report,
            "diagnostics": [item.model_dump(mode="json") for item in report.diagnostics],
            "react_trace": [*state.get("react_trace", []), {"phase": "observation", **observation_event}],
            "status": report.status,
            "events": events,
            "error": None,
        }
    )

    if report.status == "pass":
        return "finish"
    if state["iteration"] >= state["max_iterations"]:
        return "iteration_limit"
    previous = state.get("report_before_repair")
    if report.status == "unknown" or previous is None:
        return "rollback"
    if not _bad_ids(report) <= _bad_ids(previous) or _objective(report) >= _objective(previous):
        return "rollback"
    # The observation is an improvement. The next loop iteration starts with
    # a fresh Geometry Critic before the LLM is allowed to reason again.
    return "critic"


def _rollback_repair(
    state: RepairLoopState,
    emit: Callable[[str, dict[str, Any]], None],
) -> str:
    before_scene = state.get("scene_before_repair")
    before_report = state.get("report_before_repair")
    action = state.get("action")
    if before_scene is None or before_report is None or action is None:
        state.update({"status": "blocked", "error": "rollback_state_missing"})
        state["events"] = _record(state, "REPAIR_REJECTED", {"reason": "rollback_state_missing"}, emit)
        return "blocked"

    payload = {
        "repair_action_id": action.repair_action_id,
        "restore_revision": before_scene.revision,
        "reason": "new_or_more_severe_violation",
    }
    events = _record(state, "ROLLBACK_REQUESTED", payload, emit)
    events = _record({**state, "events": events}, "ROLLBACK_EXECUTED", payload, emit)
    history = [
        *state.get("history", []),
        {"iteration": state["iteration"], "repair_action_id": action.repair_action_id, "status": "rolled_back"},
    ]
    state.update(
        {
            "scene": before_scene,
            "report": before_report,
            "diagnostics": [item.model_dump(mode="json") for item in before_report.diagnostics],
            "scene_before_repair": None,
            "report_before_repair": None,
            "verification_report": before_report,
            "selection": None,
            "action": None,
            "tool_result": {"status": "rolled_back", "repair_action_id": action.repair_action_id},
            "actions": [item for item in state.get("actions", []) if item.repair_action_id != action.repair_action_id],
            "history": history,
            "events": events,
            "react_trace": [*state.get("react_trace", []), {"phase": "rollback", **payload}],
            "status": "rolled_back",
            "error": None,
        }
    )
    return "critic"


def _mark_blocked(state: RepairLoopState, emit: Callable[[str, dict[str, Any]], None]) -> None:
    payload = {"status": "blocked", "error": state.get("error"), "scene_revision": state["scene"].revision}
    state["status"] = "blocked"
    state["events"] = _record(state, "TASK_BLOCKED", payload, emit)


def _mark_iteration_limit(state: RepairLoopState, emit: Callable[[str, dict[str, Any]], None]) -> None:
    payload = {"status": "iteration_limit", "scene_revision": state["scene"].revision}
    state["status"] = "iteration_limit"
    state["events"] = _record(state, "TASK_BLOCKED", payload, emit)


def run_repair_loop(
    scene: SceneSnapshot,
    verifier: DeterministicGeometryVerifier | None = None,
    router: GeometryRepairRouter | None = None,
    *,
    registry: RepairToolRegistry | None = None,
    max_iterations: int | None = None,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    user_message: str | None = None,
) -> RepairResult:
    """Run the explicit Geometry Critic → ReAct Tool loop.

    ``max_iterations`` is always capped at ten, and one iteration means one
    deterministic tool execution followed by verification.
    """

    verifier = verifier or DeterministicGeometryVerifier()
    iterations = max_iterations if max_iterations is not None else verifier.config.maximum_iterations
    iterations = min(iterations, REACT_MAX_ITERATIONS)
    if iterations < 1:
        raise ValueError("max_iterations must be positive")

    event_sink = emit or (lambda kind, payload: None)
    tool_registry = registry or (router.registry if router is not None else default_repair_tool_registry(verifier.config))
    router_holder = {"value": router}
    state = _initial_state(scene, iterations)
    if emit is not None:
        emit("SCENE_INPUT", scene.model_dump(mode="json"))

    while True:
        _geometry_critic(state, verifier, event_sink)
        if state["status"] == "pass":
            break
        if state["status"] != "fail":
            _mark_blocked(state, event_sink)
            break
        if state["iteration"] >= state["max_iterations"]:
            _mark_blocked(state, event_sink)
            break

        route_transition = _route_tool(
            state,
            router_holder,
            tool_registry,
            event_sink,
            user_message=user_message,
        )
        if route_transition == "iteration_limit":
            _mark_iteration_limit(state, event_sink)
            break
        if route_transition == "blocked":
            _mark_blocked(state, event_sink)
            break

        execute_transition = _execute_tool(state, tool_registry, event_sink)
        if execute_transition == "blocked":
            _mark_blocked(state, event_sink)
            break

        verification_transition = _verify_scene(state, verifier, event_sink)
        if verification_transition == "finish":
            break
        if verification_transition == "iteration_limit":
            _mark_iteration_limit(state, event_sink)
            break
        if verification_transition == "rollback":
            rollback_transition = _rollback_repair(state, event_sink)
            if rollback_transition == "blocked":
                _mark_blocked(state, event_sink)
                break
        # ``critic`` and a successful rollback both continue to the top of the
        # loop, where a new Geometry Critic observation is mandatory.

    report = state.get("report")
    if report is None:
        report = verifier.diagnose(state["scene"])
    result = RepairResult(
        status=state.get("status", "blocked"),
        initial_revision=scene.revision,
        scene=state["scene"],
        report=report,
        actions=tuple(state.get("actions", [])),
        react_trace=tuple(state.get("react_trace", [])),
        iterations=state.get("iteration", 0),
        error=state.get("error"),
    )
    if emit is not None:
        emit("FINAL_RESULT", result.model_dump(mode="json"))
    return result


__all__ = ["REACT_MAX_ITERATIONS", "RepairLoopState", "run_repair_loop"]
