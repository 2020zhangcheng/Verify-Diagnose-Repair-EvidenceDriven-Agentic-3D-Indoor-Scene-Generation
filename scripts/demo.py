"""Run against Compose's HTTP API and print the verified durable event chain."""
import json
import os
import time
from uuid import uuid4
import httpx


def main():
    base = os.getenv("ROOMSCOUT_API_URL", "http://localhost:8000")
    headers = {"Authorization": f"Bearer {os.getenv('DEMO_TOKEN', 'roomscout-local-demo')}", "Idempotency-Key": str(uuid4())}
    with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
        response = client.post("/tasks", json={"request": "把书桌移动到窗户附近"})
        response.raise_for_status()
        ident = response.json()["task_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = client.get(f"/tasks/{ident}")
            response.raise_for_status()
            result = response.json()
            if result["status"] in ("finished", "failed", "blocked"):
                break
            time.sleep(.2)
        else:
            raise RuntimeError(f"Task did not finish: {ident}")
        assert result["status"] == "finished", result
        rows, after = [], 0
        while True:
            page = client.get(f"/tasks/{ident}/events", params={"after": after}).json()
            rows.extend(page["items"])
            if page["next_cursor"] is None:
                break
            after = page["next_cursor"]
        kinds = [r["type"] for r in rows]
        expected = ["TASK_CREATED", "OBSERVATION_CREATED", "BELIEF_UPDATED", "LAYOUT_GENERATED", "UNKNOWN_DETECTED",
                    "VIEW_SELECTED", "NEW_OBSERVATION", "BELIEF_UPDATED", "VERIFICATION", "ACTION_EXECUTED", "TASK_FINISHED"]
        start = 0
        for kind in expected:
            start = kinds.index(kind, start) + 1
        assert kinds.count("ACTION_EXECUTED") == 1
        trace = client.get(f"/tasks/{ident}/trace")
        trace.raise_for_status()
        print(json.dumps({"task": result, "event_count": len(rows), "event_chain": kinds,
                          "trace_nodes": len(trace.json()["nodes"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
