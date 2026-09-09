"""Geometry Critic tool catalog, router and explicit ReAct loop."""

__all__ = [
    "REPAIR_TOOL_NAMES",
    "RepairAction",
    "RepairResult",
    "RepairRequest",
    "RepairTool",
    "RepairToolSelection",
    "RepairToolRegistry",
    "default_repair_tool_registry",
    "repair_tool_schemas",
    "run_repair_loop",
]


def __getattr__(name):
    if name in {"REPAIR_TOOL_NAMES", "repair_tool_schemas"}:
        from app.repair.catalog import REPAIR_TOOL_NAMES, repair_tool_schemas

        return {"REPAIR_TOOL_NAMES": REPAIR_TOOL_NAMES, "repair_tool_schemas": repair_tool_schemas}[name]
    if name in {"RepairAction", "RepairResult", "RepairRequest", "RepairTool", "RepairToolSelection"}:
        from app.repair.models import RepairAction, RepairRequest, RepairResult, RepairTool, RepairToolSelection

        return {
            "RepairAction": RepairAction,
            "RepairResult": RepairResult,
            "RepairRequest": RepairRequest,
            "RepairTool": RepairTool,
            "RepairToolSelection": RepairToolSelection,
        }[name]
    if name in {"RepairToolRegistry", "default_repair_tool_registry"}:
        from app.repair.registry import RepairToolRegistry, default_repair_tool_registry

        return {"RepairToolRegistry": RepairToolRegistry, "default_repair_tool_registry": default_repair_tool_registry}[name]
    if name == "run_repair_loop":
        from app.repair.loop import run_repair_loop

        return run_repair_loop
    raise AttributeError(name)
