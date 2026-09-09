"""Registration, allow-list validation, and execution of repair tools."""

from __future__ import annotations

from typing import Any

from app.repair.catalog import REPAIR_TOOL_NAMES, repair_tool_schemas
from app.repair.models import RepairRequest, RepairToolSelection
from app.repair.tools import (
    DeterministicRepairTool,
    DeterministicToolError,
    RepairBoundaryTool,
    RepairSupportContactTool,
    ResolveCollisionTool,
    StabilizeSupportTool,
    ToolConfig,
    ToolOutcome,
    UnsupportedV0Tool,
)
from app.verification.models import DiagnosisReport, SceneSnapshot


class RepairToolRegistry:
    """The only entry point through which the graph can execute a repair."""

    repair_names = REPAIR_TOOL_NAMES[:6]
    control_names = REPAIR_TOOL_NAMES[6:]

    def __init__(self, tools: tuple[DeterministicRepairTool, ...], *, schemas: list[dict[str, Any]] | None = None):
        self._tools = {tool.name: tool for tool in tools}
        self._schemas = schemas if schemas is not None else repair_tool_schemas()
        missing = [name for name in self.repair_names if name not in self._tools]
        if missing:
            raise ValueError(f"missing repair tools: {missing}")

    @property
    def names(self) -> tuple[str, ...]:
        return REPAIR_TOOL_NAMES

    @property
    def schemas(self) -> list[dict[str, Any]]:
        # Return a copy so a provider adapter cannot mutate the registry.
        return [dict(item) for item in self._schemas]

    @property
    def tool_schemas(self) -> list[dict[str, Any]]:
        """Compatibility/readability alias used by adapter integrations."""

        return self.schemas

    def get(self, name: str) -> DeterministicRepairTool:
        try:
            return self._tools[name]
        except KeyError:
            raise DeterministicToolError("unknown_repair_tool") from None

    def validate_selection(self, report: DiagnosisReport, selection: RepairToolSelection):
        if selection.tool not in self.repair_names:
            raise DeterministicToolError("graph_controls_tool_lifecycle")
        diagnostic = next(
            (item for item in report.diagnostics if item.diagnosis_id == selection.selected_diagnosis_id),
            None,
        )
        if diagnostic is None:
            raise DeterministicToolError("unknown_diagnosis")
        if diagnostic.status != "fail":
            raise DeterministicToolError("diagnosis_not_failed")
        if selection.tool not in diagnostic.allowed_repair_tools:
            raise DeterministicToolError("tool_not_allowed_by_geometry_critic")
        tool = self.get(selection.tool)
        if not tool.can_handle(diagnostic):
            raise DeterministicToolError("tool_cannot_handle_diagnosis")
        target_key = "movable_object_id" if selection.tool in {"resolve_collision", "repair_clearance"} else "object_id"
        target = selection.arguments.get(target_key)
        if not isinstance(target, str) or target not in diagnostic.editable_objects:
            raise DeterministicToolError("target_not_editable")
        if target in diagnostic.locked_objects:
            raise DeterministicToolError("target_is_locked")
        return diagnostic

    def execute(self, scene: SceneSnapshot, report: DiagnosisReport, selection: RepairToolSelection) -> ToolOutcome:
        diagnostic = self.validate_selection(report, selection)
        args = dict(selection.arguments)
        # The revision is graph-owned, not model-owned.  This prevents a model
        # from fabricating a revision while keeping the public request contract.
        args.pop("scene_revision", None)
        args["diagnosis_id"] = selection.selected_diagnosis_id
        request = RepairRequest(scene_revision=scene.revision, **args)
        return self.get(selection.tool).solve(scene, diagnostic, request)


def default_repair_tool_registry(config=None) -> RepairToolRegistry:
    if config is None:
        from app.verification.geometry import load_config

        config = load_config()
    tool_config = ToolConfig(
        penetration_tolerance_m=config.penetration_tolerance_m,
        contact_tolerance_m=config.contact_tolerance_m,
        minimum_support_margin_m=config.minimum_support_margin_m,
        maximum_move_m=config.maximum_move_m,
    )
    return RepairToolRegistry(
        (
            ResolveCollisionTool(tool_config),
            RepairSupportContactTool(tool_config),
            StabilizeSupportTool(tool_config),
            RepairBoundaryTool(tool_config),
            UnsupportedV0Tool("repair_clearance", {"door_clearance", "path_blocked", "spacing_too_small"}),
            UnsupportedV0Tool("repair_orientation", {"orientation", "upright", "normal_mismatch"}),
        )
    )
