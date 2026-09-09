import json

import httpx
import pytest

from app.repair.router import GeometryRepairRouter
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.io import load_snapshot
from app.verification.llm import LLMRepairError, LLMSettings


def settings(**kwargs):
    return LLMSettings(
        _env_file=None,
        base_url="https://model.example/v1",
        model="test-model",
        api_key="unit-test-secret",
        **kwargs,
    )


def scene():
    return load_snapshot("configs/geometry-diagnosis-demo.json")


def report():
    value = scene()
    return DeterministicGeometryVerifier().diagnose(value)


def selection_response(diagnostic):
    tool = diagnostic["allowed_repair_tools"][0]
    target = diagnostic["editable_objects"][0]
    arguments = {"diagnosis_id": diagnostic["diagnosis_id"]}
    arguments["movable_object_id" if tool == "resolve_collision" else "object_id"] = target
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


def test_router_sends_diagnostics_and_same_tool_list_in_both_protocol_slots():
    events = []

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        assert payload["tools"] == context["tools"]
        diagnostic = next(item for item in context["diagnostics"] if item["allowed_repair_tools"])
        return selection_response(diagnostic)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = GeometryRepairRouter(settings(), client=client, emit=lambda kind, payload: events.append((kind, payload)))
        value = scene()
        selection = router.route(value, report())

    assert selection.tool == "resolve_collision"
    assert router.calls == 1
    assert all("unit-test-secret" not in json.dumps(payload) for _, payload in events)


def test_router_accepts_structured_json_fallback():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "selected_diagnosis_id": "collision:chair_a,chair_b",
                                    "tool": "resolve_collision",
                                    "arguments": {"movable_object_id": "chair_a"},
                                    "reason_code": "structured",
                                }
                            )
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        chosen = GeometryRepairRouter(settings(), client=client).route(scene(), report())
    assert chosen.tool == "resolve_collision"
    assert chosen.arguments["diagnosis_id"] == "collision:chair_a,chair_b"


@pytest.mark.parametrize(
    "response, expected",
    [
        (httpx.Response(401, json={"error": "bad key"}), "llm_http_401"),
        (httpx.Response(200, json={"choices": []}), "llm_invalid_response"),
    ],
)
def test_router_rejects_invalid_provider_responses(response, expected):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(LLMRepairError, match=expected):
            GeometryRepairRouter(settings(), client=client).route(scene(), report())


def test_unconfigured_router_is_rejected():
    with pytest.raises(LLMRepairError, match="not_configured"):
        GeometryRepairRouter(LLMSettings(_env_file=None, base_url="", model=""))
