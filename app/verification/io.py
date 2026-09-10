"""JSON scene input helpers."""

import json
from pathlib import Path

from app.verification.models import SceneSnapshot


def load_snapshot(path):
    path = Path(path)
    return parse_snapshot(json.loads(path.read_text()), path.stem)


def parse_snapshot(raw, scene_id="scene") -> SceneSnapshot:
    """Parse either a SceneSnapshot JSON object or a compact object list."""

    if isinstance(raw, list):
        raw = {"scene_id": scene_id, "objects": raw}
    raw = dict(raw)
    objects = []
    for item in raw["objects"]:
        item = dict(item)
        if "geometry" not in item:
            position = item.pop("position_m")
            size = item.pop("size_m")
            rotation = item.pop("orientation_xyzw", (0, 0, 0, 1))
            item["geometry"] = {
                "pose": {
                    "frame_id": raw.get("frame_id", "world"),
                    "position_m": position,
                    "orientation_xyzw": rotation,
                },
                "size_m": size,
            }
        objects.append(item)
    raw["objects"] = objects
    return SceneSnapshot.model_validate(raw)
