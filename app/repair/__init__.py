"""Diagnosis-driven deterministic repair tools and their explicit ReAct runner.

The repair package deliberately keeps the LLM at the routing boundary.  Tool
implementations calculate transforms from the critic evidence; the model never
gets a free-form translate/place operation.
"""

__all__ = [
    "REPAIR_TOOL_NAMES",
    "RepairAction",
    "RepairGraphResult",
    "RepairRequest",
    "RepairTool",
    "RepairToolSelection",
    "RepairToolRegistry",
    "default_repair_tool_registry",
    "repair_tool_schemas",
    "run_repair_loop",
    "run_repair_graph",
]


def __getattr__(name):
    """Keep package imports lazy so the critic can use the data catalog."""

    if name in {"REPAIR_TOOL_NAMES", "repair_tool_schemas"}:
        from app.repair.catalog import REPAIR_TOOL_NAMES, repair_tool_schemas

        return {"REPAIR_TOOL_NAMES": REPAIR_TOOL_NAMES, "repair_tool_schemas": repair_tool_schemas}[name]
    if name in {"RepairAction", "RepairGraphResult", "RepairRequest", "RepairTool", "RepairToolSelection"}:
        from app.repair.models import RepairAction, RepairGraphResult, RepairRequest, RepairTool, RepairToolSelection

        return {
            "RepairAction": RepairAction,
            "RepairGraphResult": RepairGraphResult,
            "RepairRequest": RepairRequest,
            "RepairTool": RepairTool,
            "RepairToolSelection": RepairToolSelection,
        }[name]
    if name in {"RepairToolRegistry", "default_repair_tool_registry"}:
        from app.repair.registry import RepairToolRegistry, default_repair_tool_registry

        return {"RepairToolRegistry": RepairToolRegistry, "default_repair_tool_registry": default_repair_tool_registry}[name]
    if name in {"run_repair_loop", "run_repair_graph"}:
        from app.repair.graph import run_repair_loop, run_repair_graph

        return {"run_repair_loop": run_repair_loop, "run_repair_graph": run_repair_graph}[name]
    raise AttributeError(name)
