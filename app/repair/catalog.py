"""The public tool catalog exposed to the Geometry Repair Planner.

This is intentionally data-only.  Keeping the catalog independent from the
deterministic implementations lets the Geometry Critic attach an allow-list
without importing the executor and avoids a critic/tool circular dependency.
"""

from __future__ import annotations

from typing import Any


REPAIR_TOOL_NAMES = (
    "resolve_collision",
    "repair_support_contact",
    "stabilize_support",
    "repair_boundary",
    "repair_clearance",
    "repair_orientation",
    "verify_scene",
    "rollback_repair",
)


def _function(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_DIAGNOSIS_ID = {
    "type": "string",
    "description": "Exact diagnosis_id from the current Geometry Critic report.",
}
_OBJECT_ID = {
    "type": "string",
    "description": "An object_id from editable_objects; never a locked object.",
}
_STRATEGY = {
    "type": "string",
    "enum": ["minimum_displacement", "preserve_anchor", "move_lower_priority"],
    "default": "minimum_displacement",
}


def repair_tool_schemas(*, include_control_tools: bool = True) -> list[dict[str, Any]]:
    """Return OpenAI-compatible function tool definitions.

    The list is rebuilt for every request so callers can safely serialize it
    into both the HTTP ``tools`` field and the JSON context shown to the LLM.
    ``verify_scene`` and ``rollback_repair`` are included because they are part
    of the documented tool contract, although the ReAct loop controls when those
    lifecycle operations are legal.
    """

    tools = [
        _function(
            "resolve_collision",
            "Resolve collision or penetration using the deterministic minimum separation vector. "
            "Do not calculate dx/dy/dz; choose the movable object and strategy only.",
            {
                "diagnosis_id": _DIAGNOSIS_ID,
                "movable_object_id": _OBJECT_ID,
                "strategy": _STRATEGY,
            },
            ["diagnosis_id", "movable_object_id"],
        ),
        _function(
            "repair_support_contact",
            "Snap a floating object to its declared floor or box support. "
            "The deterministic tool calculates the vertical displacement.",
            {
                "diagnosis_id": _DIAGNOSIS_ID,
                "object_id": _OBJECT_ID,
                "strategy": {
                    "type": "string",
                    "enum": ["snap_to_support"],
                    "default": "snap_to_support",
                },
            },
            ["diagnosis_id", "object_id"],
        ),
        _function(
            "stabilize_support",
            "Move an object within the legal support plane so its footprint and center of mass "
            "satisfy the support margin. The tool calculates the legal XY position.",
            {
                "diagnosis_id": _DIAGNOSIS_ID,
                "object_id": _OBJECT_ID,
                "strategy": {
                    "type": "string",
                    "enum": ["nearest_valid", "center_on_support", "maximize_support_margin"],
                    "default": "nearest_valid",
                },
            },
            ["diagnosis_id", "object_id"],
        ),
        _function(
            "repair_boundary",
            "Project an object back into the nearest legal room/floor domain while preserving "
            "unnecessary axes and orientation.",
            {
                "diagnosis_id": _DIAGNOSIS_ID,
                "object_id": _OBJECT_ID,
                "strategy": {
                    "type": "string",
                    "enum": ["nearest_valid", "preserve_orientation"],
                    "default": "nearest_valid",
                },
            },
            ["diagnosis_id", "object_id"],
        ),
        _function(
            "repair_clearance",
            "Find the nearest transform that satisfies door, path, or spacing clearance. "
            "The deterministic solver searches the forbidden region; do not provide a distance.",
            {
                "diagnosis_id": _DIAGNOSIS_ID,
                "movable_object_id": _OBJECT_ID,
                "strategy": {
                    "type": "string",
                    "enum": ["minimum_displacement", "preserve_layout_priority"],
                    "default": "minimum_displacement",
                },
            },
            ["diagnosis_id", "movable_object_id"],
        ),
        _function(
            "repair_orientation",
            "Align an object to the deterministic target orientation derived from up-vector, "
            "normal, or axis constraints; never invent Euler angles or a quaternion.",
            {
                "diagnosis_id": _DIAGNOSIS_ID,
                "object_id": _OBJECT_ID,
                "strategy": {
                    "type": "string",
                    "enum": ["upright", "align_normal", "align_axis"],
                    "default": "upright",
                },
            },
            ["diagnosis_id", "object_id"],
        ),
    ]
    if include_control_tools:
        tools.extend(
            [
                _function(
                    "verify_scene",
                    "Run the complete Geometry Critic for the current scene revision and return PASS or diagnostics.",
                    {
                        "scene_revision": {
                            "type": "string",
                            "description": "Current scene revision; supplied by the ReAct loop.",
                        }
                    },
                    ["scene_revision"],
                ),
                _function(
                    "rollback_repair",
                    "Restore the scene revision before a repair that introduced a new or more severe violation.",
                    {
                        "repair_action_id": {
                            "type": "string",
                            "description": "Repair action ID from the ReAct history.",
                        }
                    },
                    ["repair_action_id"],
                ),
            ]
        )
    return tools


def allowed_repair_tools_for(
    rule_id: str,
    status: str,
    measurements: dict[str, Any],
    *,
    contact_tolerance_m: float = 0.001,
) -> tuple[str, ...]:
    """Map a critic diagnosis to the smallest safe repair allow-list."""

    if status != "fail":
        return ()
    if rule_id in {"collision", "penetration"}:
        return ("resolve_collision",)
    if rule_id == "floor_penetration":
        return ("repair_boundary",)
    if rule_id in {"support", "floating", "support_gap"}:
        gap = measurements.get("gap_m")
        contact = measurements.get("contact")
        margin = measurements.get("support_margin_m")
        minimum_margin = measurements.get("minimum_margin_m")
        if isinstance(gap, (int, float)) and abs(gap) > contact_tolerance_m:
            return ("repair_support_contact",)
        if contact is False or (
            isinstance(margin, (int, float))
            and isinstance(minimum_margin, (int, float))
            and margin < minimum_margin
        ):
            return ("stabilize_support",)
        return ("repair_support_contact",)
    if rule_id in {"support_chain", "support_instability"}:
        return ("stabilize_support",)
    if rule_id in {"out_of_room", "wall_penetration"}:
        return ("repair_boundary",)
    if rule_id in {"door_clearance", "path_blocked", "spacing_too_small"}:
        return ("repair_clearance",)
    if rule_id in {"orientation", "upright", "normal_mismatch"}:
        return ("repair_orientation",)
    return ()
