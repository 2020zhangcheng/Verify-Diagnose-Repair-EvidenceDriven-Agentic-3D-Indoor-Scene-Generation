import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.api import geometry
from app.config import settings as app_settings
from app.main import app
from app.repair.router import GeometryRepairRouter
from app.verification.replay import replay_journal


AUTH = {"Authorization": f"Bearer {app_settings.demo_token}"}


def body():
    return {
        "scene": json.loads(Path("configs/geometry-diagnosis-demo.json").read_text()),
        "max_iterations": 10,
    }


def configure(monkeypatch):
    monkeypatch.setenv("ROOMSCOUT_LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("ROOMSCOUT_LLM_MODEL", "test-model")
    monkeypatch.setenv("ROOMSCOUT_LLM_API_KEY", "memory-test-secret")


def test_geometry_repair_graph_uses_memory_store(monkeypatch, tmp_path):
    configure(monkeypatch)
    geometry.geometry_store.clear()
    calls = []

    def handler(request):
        body_data = json.loads(request.content)
        context = json.loads(body_data["messages"][1]["content"])
        diagnosis = next(item for item in context["diagnostics"] if item["allowed_repair_tools"])
        tool = diagnosis["allowed_repair_tools"][0]
        arguments = {"diagnosis_id": diagnosis["diagnosis_id"]}
        if tool in {"resolve_collision", "repair_clearance"}:
            arguments["movable_object_id"] = diagnosis["editable_objects"][0]
        else:
            arguments["object_id"] = diagnosis["editable_objects"][0]
        if tool == "resolve_collision":
            arguments["strategy"] = "minimum_displacement"
        if tool == "stabilize_support":
            arguments["strategy"] = "center_on_support"
        calls.append(body_data)
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

    with httpx.Client(transport=httpx.MockTransport(handler)) as provider:
        monkeypatch.setattr(
            geometry,
            "GeometryRepairRouter",
            lambda config, **kwargs: GeometryRepairRouter(config, client=provider, **kwargs),
        )
        with TestClient(app) as client:
            headers = {**AUTH, "Idempotency-Key": "memory-react-001"}
            response = client.post("/geometry/repair-graph", headers=headers, json=body())
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["status"] == "pass"
            assert result["result"]["status"] == "pass"
            assert result["result"]["iterations"] == 3
            assert len(calls) == 3

            # The second request is served from process-local idempotency state.
            assert client.post("/geometry/repair-graph", headers=headers, json=body()).json() == result
            changed = body()
            changed["max_iterations"] = 1
            assert client.post("/geometry/repair-graph", headers=headers, json=changed).status_code == 409

            events = []
            after = 0
            while True:
                page = client.get(result["events_url"], headers=AUTH, params={"after": after, "limit": 7})
                assert page.status_code == 200
                data = page.json()
                events.extend(data["items"])
                if data["next_cursor"] is None:
                    break
                after = data["next_cursor"]

    assert events[0]["type"] == "USER_REQUEST"
    assert events[-1]["type"] == "FINAL_RESULT"
    assert sum(item["type"] == "LLM_REQUEST" for item in events) == 3
    assert "memory-test-secret" not in json.dumps(events)
    journal = tmp_path / "events.jsonl"
    journal.write_text("\n".join(json.dumps(item) for item in events))
    assert replay_journal(journal).model_dump(mode="json") == result["result"]
    geometry.geometry_store.clear()
