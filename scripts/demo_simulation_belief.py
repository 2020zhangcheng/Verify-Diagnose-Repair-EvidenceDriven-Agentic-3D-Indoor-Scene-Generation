"""Run the real simulator -> oracle perception -> structured belief chain."""
import asyncio
import argparse
import json
from pathlib import Path
from app.environment.minimal import MinimalEnvironmentAdapter
from app.perception.simulation import SimulationPerception
from app.scene.belief import InMemoryObservationArchive, StructuredSceneBeliefService
from app.contracts.models import OperationContext, TaskSpec


async def run(directory):
    env = MinimalEnvironmentAdapter('simulation-belief-demo', Path(directory)/'environment')
    perception = SimulationPerception(env.scene, Path(directory)/'perception')
    archive = InMemoryObservationArchive()
    service = StructuredSceneBeliefService(archive.load)
    task = TaskSpec(task_id=env.task_id, objective='Observe the room', constraints=(), seed=0, config_id='scene-belief-v1')
    def ctx(name):
        return OperationContext(task_id=env.task_id, run_id='demo', operation_id=name, expected_environment_revision=env.revision, fencing_token=1)
    raw = await env.observe(ctx('initial'))
    first = await perception.interpret(raw)
    archive.add(first)
    before = await service.update(None, (first,), task)
    moved = await env.move_camera(raw.camera_pose.model_copy(update={'position_m':(1.5,-2,1.5)}), ctx('move'))
    assert moved.status == 'succeeded'
    second = await perception.interpret(await env.observe(ctx('second')))
    archive.add(second)
    after = await service.update(before, (second,), task)
    replay = await service.update(None, (second,first), task)
    assert service._content(replay) == service._content(after)
    result = {'before':before.model_dump(mode='json'), 'after':after.model_dump(mode='json'),
              'observations':[first.model_dump(mode='json'), second.model_dump(mode='json')], 'replay_matches':True}
    Path(directory,'belief-demo.json').write_text(json.dumps(result,indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',default='outputs/simulation-belief-demo')
    result = asyncio.run(run(parser.parse_args().output))
    print(json.dumps({'objects_before':[o['object_id'] for o in result['before']['objects']],
                      'objects_after':[o['object_id'] for o in result['after']['objects']], 'replay_matches':result['replay_matches']},indent=2))
