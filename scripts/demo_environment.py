"""Standalone adapter demo: real generated artifacts, no Planning/Memory/Graph."""
import argparse
import asyncio
import json
from pathlib import Path
from app.environment.minimal import MinimalEnvironmentAdapter
from app.contracts.models import OperationContext, CandidateLayout, BeliefRef, Placement


async def demo(directory):
    adapter=MinimalEnvironmentAdapter('environment-demo',directory)
    def ctx(ident):
        return OperationContext(operation_id=ident,task_id='environment-demo',run_id='demo',expected_environment_revision=adapter.revision,fencing_token=1)
    first=await adapter.observe(ctx('initial'))
    pose=first.camera_pose.model_copy(update={'position_m':(1.5,-2,1.5)})
    moved=await adapter.move_camera(pose,ctx('move'))
    assert moved.status=='succeeded'
    second=await adapter.observe(ctx('second'))
    layout=CandidateLayout(layout_id='collision-demo',task_id='environment-demo',belief_ref=BeliefRef(belief_id='demo-only',version=1),
        placements=(Placement(object_id='desk',target=adapter.scene.objects[1].geometry),),constraints=(),required_assumptions=(),planner_version='demo-only')
    collision=await adapter.detect_collision(layout,ctx('collision'))
    result={'initial':first.model_dump(mode='json'),'move':moved.model_dump(mode='json'),
            'second':second.model_dump(mode='json'),'collision_query':collision.model_dump(mode='json')}
    Path(directory).mkdir(parents=True,exist_ok=True)
    Path(directory,'demo.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='outputs/environment-demo')
    asyncio.run(demo(parser.parse_args().output))
