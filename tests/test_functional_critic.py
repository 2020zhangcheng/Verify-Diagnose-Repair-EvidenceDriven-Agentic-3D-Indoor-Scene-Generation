import json

import httpx

from app.repair.catalog import REPAIR_TOOL_NAMES
from app.repair.loop import run_repair_loop
from app.repair.registry import default_repair_tool_registry
from app.repair.router import GeometryRepairRouter
from app.verification.functional import FunctionalCritic, GeometryFunctionalCritic
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.io import parse_snapshot
from app.verification.llm import LLMSettings


def functional_scene():
    return parse_snapshot(
        {
            "scene_id": "functional-demo",
            "objects": [
                {
                    "object_id": "chair",
                    "position_m": [0, 0, 0.5],
                    "size_m": [1, 1, 1],
                    "support_id": "floor",
                    "affordance": {
                        "type": "sit",
                        "interaction_anchor_local_m": [0, -0.45, 0.45],
                        "front_axis_local": [0, -1, 0],
                        "clearance_size_m": [0.8, 0.8, 1.8],
                    },
                },
                {
                    "object_id": "desk",
                    "position_m": [0, -1, 0.9],
                    "size_m": [1, 0.5, 1.8],
                    "support_id": "floor",
                },
            ],
        }
    )


def test_functional_diagnosis_emits_solution_and_allowed_tool():
    report = FunctionalCritic().diagnose(functional_scene())
    diagnosis = next(item for item in report.diagnostics if item.rule_id == "functional_clearance")

    assert report.status == "fail"
    assert diagnosis.allowed_repair_tools == ("repair_clearance",)
    assert diagnosis.editable_objects == ("chair",)
    assert diagnosis.suggestions
    assert diagnosis.suggestions[0].diagnosis_id == diagnosis.diagnosis_id


def test_functional_critic_routes_catalog_and_reverifies_with_user_message():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        context = json.loads(body["messages"][1]["content"])
        assert [item["function"]["name"] for item in body["tools"]] == list(REPAIR_TOOL_NAMES)
        assert context["tools"] == body["tools"]
        assert context["user_message"] == "请让椅子可以正常使用"
        assert all("suggestions" not in item for item in context["diagnostics"])
        diagnosis = next(item for item in context["diagnostics"] if item["allowed_repair_tools"])
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
                                    "function": {
                                        "name": "repair_clearance",
                                        "arguments": json.dumps(
                                            {
                                                "diagnosis_id": diagnosis["diagnosis_id"],
                                                "movable_object_id": "chair",
                                                "strategy": "minimum_displacement",
                                            }
                                        ),
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        )

    registry = default_repair_tool_registry()
    critic = GeometryFunctionalCritic(DeterministicGeometryVerifier(), FunctionalCritic())
    settings = LLMSettings(_env_file=None, base_url="https://model.example/v1", model="test-model")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = GeometryRepairRouter(settings, client=client, registry=registry)
        result = run_repair_loop(
            functional_scene(),
            critic,
            router,
            registry=registry,
            max_iterations=5,
            user_message="请让椅子可以正常使用",
        )

    assert result.status == "pass"
    assert result.iterations == 1
    assert [action.tool_name for action in result.actions] == ["repair_clearance"]
    assert len(requests) == 1
    assert all(
        item.status == "pass"
        for item in result.report.diagnostics
        if item.rule_id.startswith("functional_") or item.rule_id == "reach_unavailable"
    )
