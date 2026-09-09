"""LLM Repair Router: tool selection only, never geometry calculation."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any

import httpx

from app.repair.catalog import repair_tool_schemas
from app.repair.models import RepairToolSelection
from app.repair.registry import RepairToolRegistry, default_repair_tool_registry
from app.verification.llm import LLMRepairError, LLMSettings
from app.verification.models import DiagnosisReport, SceneSnapshot


def build_router_system_prompt() -> str:
    """Return the request-independent planner policy from the design document."""

    return """You are the Geometry Repair Planner.
You do NOT directly modify object coordinates.
Your only job is to select an appropriate deterministic repair tool from the
tools allowed by the current Geometry Critic diagnosis.

Rules:
1. Never calculate or invent dx/dy/dz yourself.
2. Never invent absolute object coordinates, rotations, or quaternions.
3. Only choose tools listed in the selected diagnosis's allowed_repair_tools.
4. Never modify an object with movable=false or a locked object.
5. Prefer the minimum scene change that resolves the diagnosis.
6. Handle one diagnosis at a time unless diagnoses are independent.
7. After every repair, the graph calls verify_scene.
8. If the repair creates new or more severe violations, the graph calls rollback_repair.
9. Do not use delete, scale, free-form translate, or free-form place as shortcuts.
10. Finish only when Geometry Critic returns PASS.
11. Do not select verify_scene or rollback_repair directly; the graph controls these lifecycle tools.

Use a native function tool call when available. If the provider cannot emit one,
return only JSON with selected_diagnosis_id, tool, arguments, and reason_code.
The arguments contain IDs and a strategy only; deterministic tools calculate all
numeric transforms."""


class GeometryRepairRouter:
    """Call a Chat Completions-compatible model with diagnostics plus tools."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        emit=None,
        client: httpx.Client | None = None,
        registry: RepairToolRegistry | None = None,
    ):
        self.settings = settings or LLMSettings()
        if not self.settings.configured:
            raise LLMRepairError("llm_not_configured")
        self.emit = emit or (lambda kind, payload: None)
        self.client = client
        self.registry = registry or default_repair_tool_registry()
        self.calls = 0

    @property
    def tools(self):
        """The exact catalog sent in the protocol-level ``tools`` field."""

        return self.registry.schemas

    def _context(self, scene: SceneSnapshot, report: DiagnosisReport, history) -> dict[str, Any]:
        diagnostics = []
        for item in sorted(
            (diagnostic for diagnostic in report.diagnostics if diagnostic.status == "fail"),
            key=lambda diagnostic: (-diagnostic.severity, diagnostic.diagnosis_id),
        ):
            # The legacy verifier still stores MOVE suggestions for its
            # compatibility policy.  Do not expose those numeric deltas to the
            # new planner: the PDF contract gives the LLM measurements and
            # editable/locked IDs, while the Tool computes the transform.
            diagnostic = item.model_dump(mode="json")
            diagnostic.pop("suggestions", None)
            diagnostic.pop("editable_variables", None)
            diagnostics.append(diagnostic)
        schemas = self.registry.schemas
        return {
            "scene_revision": report.scene_revision,
            # IDs and editability are useful context; numeric poses/sizes stay
            # behind the deterministic solver so the planner cannot turn the
            # scene snapshot into a free-form coordinate API.
            "scene_objects": [
                {
                    "object_id": item.object_id,
                    "movable": item.movable,
                    "anchored": item.anchored,
                    "requires_support": item.requires_support,
                    "support_id": item.support_id,
                }
                for item in scene.objects
            ],
            "diagnostics": diagnostics,
            # Keep the catalog in the JSON context as well as the protocol-level
            # tools field. This makes recordings self-contained and auditable.
            "tools": schemas,
            "repair_history": list(history)[-12:],
        }

    @staticmethod
    def _arguments(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("tool arguments must be an object")
            return parsed
        raise ValueError("tool arguments must be an object")

    def _selection_from_message(self, message: dict[str, Any]) -> RepairToolSelection:
        calls = message.get("tool_calls") or []
        if calls:
            call = calls[0]
            function = call.get("function") or {}
            tool = function.get("name")
            arguments = self._arguments(function.get("arguments"))
            if not isinstance(tool, str):
                raise ValueError("tool call has no function name")
            selected_diagnosis_id = arguments.get("diagnosis_id") or arguments.get("selected_diagnosis_id")
            if not isinstance(selected_diagnosis_id, str):
                raise ValueError("tool call has no diagnosis_id")
            reason_code = arguments.pop("reason_code", "TOOL_CALL")
            return RepairToolSelection(
                selected_diagnosis_id=selected_diagnosis_id,
                tool=tool,
                arguments=arguments,
                reason_code=reason_code if isinstance(reason_code, str) else "TOOL_CALL",
            )

        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ValueError("model returned no tool selection")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("model selection must be an object")
        tool = parsed.get("tool")
        selected_diagnosis_id = parsed.get("selected_diagnosis_id")
        arguments = self._arguments(parsed.get("arguments"))
        if not isinstance(tool, str) or not isinstance(selected_diagnosis_id, str):
            raise ValueError("model selection is missing tool or diagnosis")
        if "diagnosis_id" not in arguments:
            arguments["diagnosis_id"] = selected_diagnosis_id
        reason_code = parsed.get("reason_code", "STRUCTURED_SELECTION")
        return RepairToolSelection(
            selected_diagnosis_id=selected_diagnosis_id,
            tool=tool,
            arguments=arguments,
            reason_code=reason_code if isinstance(reason_code, str) else "STRUCTURED_SELECTION",
        )

    def route(self, scene: SceneSnapshot, report: DiagnosisReport, history=()) -> RepairToolSelection:
        self.calls += 1
        call_id = f"geometry-router-{self.calls}"
        context = self._context(scene, report, history)
        body = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": build_router_system_prompt()},
                {"role": "user", "content": json.dumps(context, sort_keys=True)},
            ],
            self.settings.token_parameter: self.settings.max_tokens,
            "stream": False,
            "tools": self.registry.schemas,
            "tool_choice": "auto",
        }
        self.emit("LLM_REQUEST", {"call_id": call_id, "request": body, "settings": self.settings.public()})
        headers = {"Content-Type": "application/json"}
        api_key = self.settings.api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        started = monotonic()
        own = self.client is None
        client = self.client or httpx.Client(timeout=self.settings.timeout_seconds, follow_redirects=False)
        try:
            response = client.post(
                self.settings.base_url.rstrip("/") + "/chat/completions",
                json=body,
                headers=headers,
                timeout=self.settings.timeout_seconds,
            )
            raw = response.text
            self.emit(
                "LLM_RESPONSE",
                {
                    "call_id": call_id,
                    "status_code": response.status_code,
                    "body": raw,
                    "latency_seconds": monotonic() - started,
                },
            )
            if not 200 <= response.status_code < 300:
                raise LLMRepairError(f"llm_http_{response.status_code}")
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise LLMRepairError("llm_incomplete_or_refused")
            if choice.get("finish_reason") not in {None, "stop", "tool_calls"}:
                raise LLMRepairError("llm_incomplete_or_refused")
            selection = self._selection_from_message(message)
            self.registry.validate_selection(report, selection)
            self.emit(
                "LLM_PARSED",
                {"call_id": call_id, "selection": selection.model_dump(mode="json"), "usage": payload.get("usage")},
            )
            return selection
        except httpx.TimeoutException:
            self.emit("LLM_ERROR", {"call_id": call_id, "code": "llm_timeout"})
            raise LLMRepairError("llm_timeout") from None
        except httpx.RequestError:
            self.emit("LLM_ERROR", {"call_id": call_id, "code": "llm_connection_error"})
            raise LLMRepairError("llm_connection_error") from None
        except LLMRepairError:
            raise
        except (ValueError, KeyError, IndexError, TypeError):
            self.emit("LLM_ERROR", {"call_id": call_id, "code": "llm_invalid_response"})
            raise LLMRepairError("llm_invalid_response") from None
        finally:
            if own:
                client.close()

    # A small semantic alias for callers that describe the node as a chooser;
    # the graph itself uses ``route`` to make the planner boundary explicit.
    choose = route
