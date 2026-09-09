import json

import httpx

from app.repair.catalog import REPAIR_TOOL_NAMES, repair_tool_schemas
from app.repair.loop import run_repair_loop
from app.repair.router import GeometryRepairRouter
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.io import load_snapshot
from app.verification.llm import LLMSettings


def test_catalog_matches_design_document():
    assert REPAIR_TOOL_NAMES == (
        "resolve_collision",
        "repair_support_contact",
        "stabilize_support",
        "repair_boundary",
        "repair_clearance",
        "repair_orientation",
        "verify_scene",
        "rollback_repair",
    )
    assert [item["function"]["name"] for item in repair_tool_schemas()] == list(REPAIR_TOOL_NAMES)


def test_geometry_critic_emits_router_allow_lists():
    report = DeterministicGeometryVerifier().diagnose(load_snapshot("configs/geometry-diagnosis-demo.json"))
    failures = {item.diagnosis_id: item for item in report.diagnostics if item.status == "fail"}
    assert failures["collision:chair_a,chair_b"].allowed_repair_tools == ("resolve_collision",)
    assert failures["support:floating_box,floor"].allowed_repair_tools == ("repair_support_contact",)
    assert failures["support:vase,pedestal"].allowed_repair_tools == ("stabilize_support",)
    assert failures["support:vase,pedestal"].locked_objects == ("pedestal",)


def test_explicit_react_routes_tools_and_reverifies_every_action():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert [item["function"]["name"] for item in body["tools"]] == list(REPAIR_TOOL_NAMES)
        context = json.loads(body["messages"][1]["content"])
        assert context["tools"] == body["tools"]
        diagnosis = next(item for item in context["diagnostics"] if item["allowed_repair_tools"])
        tool = diagnosis["allowed_repair_tools"][0]
        target = diagnosis["editable_objects"][0]
        arguments = {"diagnosis_id": diagnosis["diagnosis_id"]}
        if tool in {"resolve_collision", "repair_clearance"}:
            arguments["movable_object_id"] = target
        else:
            arguments["object_id"] = target
        if tool == "resolve_collision":
            arguments["strategy"] = "minimum_displacement"
        if tool == "stabilize_support":
            arguments["strategy"] = "center_on_support"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {"name": tool, "arguments": json.dumps(arguments)},
                                }
                            ]
                        },
                    }
                ]
            },
        )

    events = []
    settings = LLMSettings(_env_file=None, base_url="https://model.example/v1", model="test-model")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = GeometryRepairRouter(settings, client=client)
        result = run_repair_loop(
            load_snapshot("configs/geometry-diagnosis-demo.json"),
            DeterministicGeometryVerifier(),
            router,
            max_iterations=5,
            emit=lambda kind, payload: events.append((kind, payload)),
        )

    assert result.status == "pass"
    assert [action.tool_name for action in result.actions] == [
        "resolve_collision",
        "repair_support_contact",
        "stabilize_support",
    ]
    assert len(requests) == 3
    assert sum(kind == "REVERIFICATION" for kind, _ in events) == 3
    assert sum(kind == "DIAGNOSIS" for kind, _ in events) == 3
    assert sum(kind == "REACT_REASON" for kind, _ in events) == 3
    assert sum(kind == "REACT_ACTION" for kind, _ in events) == 3
    assert sum(kind == "REACT_OBSERVATION" for kind, _ in events) == 3
    assert sum(kind == "FINAL_RESULT" for kind, _ in events) == 1
    assert all(action.delta_m is not None for action in result.actions)
