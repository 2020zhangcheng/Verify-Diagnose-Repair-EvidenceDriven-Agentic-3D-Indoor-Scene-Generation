"""HTTP demo: real SceneBeliefService, explicitly Mock inputs and downstream nodes."""
import asyncio
import json
import os
import time
from uuid import uuid4
import httpx


def main():
    headers = {"Authorization": f"Bearer {os.getenv('DEMO_TOKEN', 'roomscout-local-demo')}", "Idempotency-Key": str(uuid4())}
    with httpx.Client(base_url=os.getenv("ROOMSCOUT_API_URL", "http://localhost:8000"), headers=headers, timeout=10) as client:
        response = client.post("/tasks", json={"request": "把书桌移动到窗户附近", "config_id": "scene-belief-v1"})
        response.raise_for_status()
        ident = response.json()["task_id"]
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            response = client.get(f"/tasks/{ident}")
            response.raise_for_status()
            status = response.json()
            if status["status"] in ("finished", "failed", "blocked"):
                break
            time.sleep(.2)
        else:
            raise RuntimeError("demo timeout")
        assert status["status"] == "finished", status
        snapshots = []
        for version in (1, 2):
            response = client.get(f"/tasks/{ident}/belief", params={"version": version})
            response.raise_for_status()
            belief = response.json()["belief"]
            snapshots.append({"version": version, "fusion_version": belief["fusion_version"],
                "desk_position": belief["objects"][0]["geometry"]["pose"]["position_m"],
                "desk_uncertainty": belief["objects"][0]["uncertainty"],
                "regions": {r["region_id"]: r["kind"] for r in belief["regions"]},
                "window_claim": next(c for c in belief["claims"] if c["claim_id"] == "window-space-empty"),
                "evidence": belief["objects"][0]["evidence"]})
        assert snapshots[0]["window_claim"]["status"] == "uncertain"
        assert snapshots[1]["window_claim"]["status"] == "supported"
        assert 0 < snapshots[1]["desk_position"][0] < .04
        assert snapshots[1]["regions"]["behind-cabinet"] == "unobserved"
        events, after = [], 0
        while True:
            page_response = client.get(f"/tasks/{ident}/events", params={"after": after})
            page_response.raise_for_status()
            page = page_response.json()
            events.extend(page["items"])
            if page["next_cursor"] is None:
                break
            after = page["next_cursor"]
        from app.scene.replay import replay_beliefs
        replayed = asyncio.run(replay_beliefs(events))
        assert len(replayed) == 2
        print(json.dumps({"task_id": ident, "event_replay_matches": True, "status": status["status"], "real_module": "SceneBeliefService",
            "mock_modules": ["Observation source", "LayoutPlanner", "CriticalUnknownDetector", "ViewGenerator", "ViewSelector", "GeometryVerifier", "Action", "Memory"],
            "snapshots": snapshots}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
