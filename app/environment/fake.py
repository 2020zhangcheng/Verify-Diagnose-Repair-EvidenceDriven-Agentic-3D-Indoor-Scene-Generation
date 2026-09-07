"""Deterministic fake environment. No renderer, sensor or geometry algorithm."""
from datetime import datetime, timezone
from app.contracts.models import (CameraPose, Observation, ActionResult, MoveResult, GeometryEvidence, OperationStatus)

FAKE_TIME = datetime(2026, 9, 7, tzinfo=timezone.utc)


def pose(x=0):
    return CameraPose(frame_id="world", position_m=(x, 0, 1.5), orientation_xyzw=(0, 0, 0, 1))


class FakeEnvironmentAdapter:
    def __init__(self, round_number=1, selected_view_id=None, after_action=False):
        self.round_number = round_number
        self.selected_view_id = selected_view_id
        self.after_action = after_action

    async def observe(self, ctx):
        task_id, operation_id = ctx.task_id, ctx.operation_id
        round_number, selected_view_id, after_action = self.round_number, self.selected_view_id, self.after_action
        return Observation(observation_id=f"{operation_id}:obs", task_id=task_id,
                           camera_view_id=f"{selected_view_id}:executed" if selected_view_id else f"{operation_id}:camera",
                           camera_pose=pose(1 if round_number > 1 else 0), captured_at=FAKE_TIME,
                           environment_revision="fake-room-2" if after_action else "fake-room-1",
                           source="replay", artifacts=(), sensor_calibration_id="fake-calibration-v0",
                           operation_id=operation_id)

    async def execute_layout(self, layout, decision, verification, ctx):
        task_id, operation_id, checks = ctx.task_id, ctx.operation_id, verification
        if ctx.expected_environment_revision != "fake-room-1" or layout.task_id != task_id:
            raise ValueError("stale revision or task scope")
        if decision.kind != "execute" or decision.layout_id != layout.layout_id or decision.belief_ref != layout.belief_ref:
            raise ValueError("decision/layout mismatch")
        required = {c.rule_id for c in layout.constraints if c.hard}
        if not checks or {v.rule_id for v in checks} != required:
            raise ValueError("incomplete verification")
        for check in checks:
            if (check.status != "pass" or check.belief_ref != layout.belief_ref
                    or check.layout_id != layout.layout_id or check.environment_revision != "fake-room-1"):
                raise ValueError("stale or failed verification")
        return ActionResult(action_id=f"{operation_id}:action", task_id=task_id, operation_id=operation_id,
                            decision_id=decision.decision_id, layout_id=layout.layout_id,
                            status="succeeded", before_revision="fake-room-1", after_revision="fake-room-2",
                            verification_ids=tuple(v.verification_id for v in checks))

    async def move_camera(self, pose, ctx):
        return MoveResult(operation_id=ctx.operation_id, status="succeeded", camera_view_id=self.selected_view_id,
                          actual_pose=pose, environment_revision=ctx.expected_environment_revision)

    async def get_depth(self, observation):
        return None

    async def measure_geometry(self, query, ctx):
        return GeometryEvidence(environment_revision=ctx.expected_environment_revision, status="unsupported")

    async def detect_collision(self, layout, ctx):
        return GeometryEvidence(environment_revision=ctx.expected_environment_revision, status="unsupported")

    async def reconcile(self, ctx):
        # Pure scripted operations have no external state. Durable wrapper owns the result ledger.
        return OperationStatus(operation_id=ctx.operation_id, status="not_started")
