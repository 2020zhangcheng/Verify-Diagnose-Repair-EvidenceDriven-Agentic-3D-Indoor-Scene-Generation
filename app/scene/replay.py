"""Read-only replay of this module from event data, independent of live services."""
from app.contracts.models import Observation, TaskSpec, SceneBelief
from app.scene.config import FusionConfig
from app.scene.belief import InMemoryObservationArchive, StructuredSceneBeliefService, FusionInputError


async def replay_beliefs(events):
    archive = InMemoryObservationArchive()
    previous, task = None, None
    configs, results = {}, []
    for event in sorted(events, key=lambda e: e["sequence"]):
        payload = event["payload"]
        if event["type"] == "TASK_UNDERSTOOD":
            task = TaskSpec.model_validate(payload["entity"])
        elif event["type"] == "OBSERVATION_CREATED":
            archive.add(Observation.model_validate(payload["entity"]))
        elif event["type"] == "TOOL_CALL" and payload["tool_name"] == "scene.update":
            config = FusionConfig.model_validate(payload["arguments"]["fusion_config"])
            if payload["arguments"]["fusion_version"] != config.version:
                raise FusionInputError("recorded configuration hash mismatch")
            configs[config.version] = config
        elif event["type"] == "BELIEF_UPDATED":
            expected = SceneBelief.model_validate(payload["entity"])
            if task is None or expected.fusion_version not in configs:
                raise FusionInputError("missing recorded task/fusion configuration; V0 Mock is not replayed as real fusion")
            new_ids = tuple(i for i in expected.observation_ids if previous is None or i not in previous.observation_ids)
            observations = await archive.load(task.task_id, new_ids)
            service = StructuredSceneBeliefService(archive.load, configs[expected.fusion_version])
            previous = await service.update(previous, observations, task)
            if previous != expected:
                raise FusionInputError("replayed belief differs from recorded result")
            results.append(previous)
    if not results:
        raise FusionInputError("no belief events to replay")
    return tuple(results)
