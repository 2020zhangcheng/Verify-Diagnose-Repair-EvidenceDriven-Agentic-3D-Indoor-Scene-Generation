"""Run the restored SceneBelief fusion model with deterministic fake observations."""

import argparse
import asyncio
import json
from pathlib import Path

from app.contracts.models import OperationContext, TaskSpec
from app.environment.fake import FakeEnvironmentAdapter
from app.scene.belief import InMemoryObservationArchive, StructuredSceneBeliefService
from app.scene.fixtures import structured_observation


async def run(directory):
    task = TaskSpec(
        task_id="scene-belief-demo",
        objective="Move desk by window",
        constraints=(),
        seed=0,
        config_id="scene-belief-v1",
    )

    def context(operation_id):
        return OperationContext(
            operation_id=operation_id,
            task_id=task.task_id,
            run_id="demo",
            expected_environment_revision="fake-room-1",
            fencing_token=1,
        )

    first = structured_observation(await FakeEnvironmentAdapter(1).observe(context("first")), 1)
    second = structured_observation(await FakeEnvironmentAdapter(2).observe(context("second")), 2)
    archive = InMemoryObservationArchive((first, second))
    service = StructuredSceneBeliefService(archive.load)
    before = await service.update(None, (first,), task)
    after = await service.update(before, (second,), task)
    replay = await service.update(None, (second, first), task)
    assert service._content(replay) == service._content(after)

    result = {
        "before": before.model_dump(mode="json"),
        "after": after.model_dump(mode="json"),
        "replay_matches": True,
    }
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "belief-demo.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/scene-belief-demo")
    result = asyncio.run(run(parser.parse_args().output))
    print(
        json.dumps(
            {
                "objects_before": [item["object_id"] for item in result["before"]["objects"]],
                "objects_after": [item["object_id"] for item in result["after"]["objects"]],
                "replay_matches": result["replay_matches"],
            },
            indent=2,
        )
    )
