"""Deterministic replay for an explicit ReAct repair journal."""

from __future__ import annotations

import json
from pathlib import Path

from app.repair.models import RepairResult, RepairToolSelection
from app.repair.registry import default_repair_tool_registry
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.models import SceneSnapshot, VerifierConfig


def replay_journal(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    verifier = None
    registry = None
    scene = None
    report = None
    selection = None
    pending = None
    before = None
    actions = []
    final = None
    iteration = 0

    for sequence, row in enumerate(rows, 1):
        if row.get("sequence") != sequence:
            raise ValueError("journal sequence gap")
        kind = row["type"]
        payload = row["payload"]
        if kind == "CONFIG":
            verifier = DeterministicGeometryVerifier(VerifierConfig.model_validate(payload["config"]))
            if verifier.version != payload["version"]:
                raise ValueError("configuration hash mismatch")
            registry = default_repair_tool_registry(verifier.config)
        elif kind == "SCENE_INPUT":
            scene = SceneSnapshot.model_validate(payload)
            if verifier is None:
                verifier = DeterministicGeometryVerifier()
                registry = default_repair_tool_registry(verifier.config)
            report = verifier.diagnose(scene)
        elif kind == "DIAGNOSIS":
            if scene is None or verifier is None:
                raise ValueError("diagnosis before scene")
            actual = verifier.diagnose(scene)
            if actual.model_dump(mode="json") != payload:
                raise ValueError("diagnosis replay mismatch")
            report = actual
        elif kind == "TOOL_SELECTED":
            selection = RepairToolSelection.model_validate(payload)
        elif kind == "ACTION_INTENT":
            if scene is None or report is None or selection is None or registry is None:
                raise ValueError("action before tool selection")
            if payload["source_revision"] != scene.revision:
                raise ValueError("stale action")
            outcome = registry.execute(scene, report, selection)
            expected = payload["repair_action"]
            if outcome.action.model_dump(mode="json") != expected:
                raise ValueError("repair action replay mismatch")
            if outcome.scene.revision != payload["target_revision"]:
                raise ValueError("target revision mismatch")
            pending = outcome
            before = (scene, report)
        elif kind == "ACTION_EXECUTED":
            if pending is None or pending.action.model_dump(mode="json") != payload["repair_action"]:
                raise ValueError("action missing durable intent")
            if pending.scene.model_dump(mode="json") != payload["scene"]:
                raise ValueError("executed scene mismatch")
            scene = pending.scene
            actions.append(pending.action)
            iteration += 1
            pending = None
        elif kind == "REVERIFICATION":
            if scene is None or verifier is None:
                raise ValueError("verification before scene")
            actual = verifier.diagnose(scene)
            if actual.model_dump(mode="json") != payload:
                raise ValueError("verification replay mismatch")
            report = actual
        elif kind == "ROLLBACK_REQUESTED":
            if before is None:
                raise ValueError("rollback without action")
            if before[0].revision != payload["restore_revision"]:
                raise ValueError("rollback revision mismatch")
        elif kind == "ROLLBACK_EXECUTED":
            if before is None:
                raise ValueError("rollback without action")
            scene, report = before
            actions = [action for action in actions if action.repair_action_id != payload["repair_action_id"]]
            before = None
        elif kind == "FINAL_RESULT":
            final = RepairResult.model_validate(payload)
            if scene is None or report is None:
                raise ValueError("final result before scene")
            if final.scene != scene or final.report != report or final.actions != tuple(actions):
                raise ValueError("final repair replay mismatch")
        # LLM_REQUEST/LLM_RESPONSE/LLM_PARSED/TOOL_RESULT and USER_REQUEST are
        # audit records; replay never trusts them as geometry facts.

    if final is None:
        raise ValueError("journal has no completed repair result")
    return final


__all__ = ["replay_journal"]
