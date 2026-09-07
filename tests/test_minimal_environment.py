import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse,unquote
import pytest
from app.environment.minimal import MinimalEnvironmentAdapter,load_scene,UnsupportedCapability
from app.environment.geometry import intersects
from app.contracts.models import OperationContext,CandidateLayout,BeliefRef,Placement,GeometryQuery


def run(awaitable):
    return asyncio.run(awaitable)


def context(env,ident,**kwargs):
    return OperationContext(operation_id=ident,task_id=env.task_id,run_id='test',expected_environment_revision=env.revision,fencing_token=1,**kwargs)


def content(ref):
    return Path(unquote(urlparse(ref.uri).path)).read_bytes()


@pytest.fixture
def env(tmp_path):
    return MinimalEnvironmentAdapter('test',tmp_path,load_scene().model_copy(update={'width':33,'height':25}))


def layout(env,box,ident='layout'):
    return CandidateLayout(layout_id=ident,task_id=env.task_id,belief_ref=BeliefRef(belief_id='b',version=1),placements=(Placement(object_id='desk',target=box),),constraints=(),required_assumptions=(),planner_version='test')


def test_rgb_depth_calibration_occlusion(env):
    obs=run(env.observe(context(env,'obs')))
    rgb,depth,calibration=obs.artifacts
    assert content(rgb).startswith(b'\x89PNG\r\n\x1a\n')
    data=json.loads(content(depth))
    assert (len(data['depth_m']),len(data['depth_m'][0]))==(25,33)
    assert data['depth_m'][12][16]==pytest.approx(1.925)
    assert data['convention']=='camera_z' and data['unit']=='m'
    assert json.loads(content(calibration))['cx']==16.5
    assert obs.detected_objects==() and obs.observed_room_geometry is None
    assert run(env.get_depth(obs))==depth


def test_movement_and_restart_are_durable(env):
    first=run(env.observe(context(env,'first')))
    target=first.camera_pose.model_copy(update={'position_m':(1.5,-2,1.5)})
    move=run(env.move_camera(target,context(env,'move')))
    assert move.status=='succeeded'
    restarted=MinimalEnvironmentAdapter(env.task_id,env.directory.parent,env.scene)
    assert run(restarted.move_camera(target,context(env,'move')))==move
    assert run(restarted.observe(context(env,'first')))==first
    second=run(restarted.observe(context(env,'second')))
    assert second.camera_pose==target and second.camera_view_id==move.camera_view_id
    assert second.artifacts[0].sha256!=first.artifacts[0].sha256
    assert second.artifacts[1].sha256!=first.artifacts[1].sha256
    assert run(restarted.reconcile(context(env,'move'))).status=='succeeded'


@pytest.mark.parametrize('target',[(0,0,1.5),(0,2,1.5),(4,-2,1.5)])
def test_invalid_endpoint_or_obstructed_path_does_not_move(env,target):
    original=env.scene.initial_camera
    result=run(env.move_camera(original.model_copy(update={'position_m':target}),context(env,'bad')))
    assert result.status=='failed'
    assert run(env.observe(context(env,'obs'))).camera_pose==original


def test_idempotency_scope_and_fencing(env):
    ctx=context(env,'one')
    run(env.observe(ctx))
    with pytest.raises(ValueError,match='different request'):
        run(env.move_camera(env.scene.initial_camera,ctx))
    with pytest.raises(ValueError,match='revision'):
        run(env.observe(ctx.model_copy(update={'operation_id':'two','expected_environment_revision':'wrong'})))
    with pytest.raises(ValueError,match='task'):
        run(env.observe(ctx.model_copy(update={'task_id':'other'})))
    run(env.observe(ctx.model_copy(update={'fencing_token':2})))
    with pytest.raises(ValueError,match='fencing'):
        run(env.observe(context(env,'later')))


def test_collision_free_overlap_boundary_and_measure(env):
    desk=env.scene.objects[2].geometry
    free=run(env.detect_collision(layout(env,desk),context(env,'free')))
    assert not json.loads(content(free.artifacts[0]))['collision']
    occupied=run(env.detect_collision(layout(env,env.scene.objects[1].geometry,'overlap'),context(env,'overlap')))
    report=json.loads(content(occupied.artifacts[0]))
    assert report['collision'] and ['desk','radiator'] in report['pairs']
    assert report['geometry_source']=='simulator_ground_truth'
    outside=desk.model_copy(update={'pose':desk.pose.model_copy(update={'position_m':(3,1,.4)})})
    report=run(env.detect_collision(layout(env,outside,'outside'),context(env,'outside')))
    assert json.loads(content(report.artifacts[0]))['outside_room_ids']==['desk']
    query=GeometryQuery(rule_id='collision',layout_id='overlap',object_ids=())
    assert run(env.measure_geometry(query,context(env,'measure'))).artifacts==occupied.artifacts
    assert run(env.measure_geometry(query.model_copy(update={'rule_id':'walkable_path'}),context(env,'unsupported'))).status=='unsupported'


def test_oriented_box_sat_and_contact(env):
    from math import sin,cos,pi
    a=env.scene.objects[2].geometry
    b=a.model_copy(update={'pose':a.pose.model_copy(update={'orientation_xyzw':(0,0,sin(pi/8),cos(pi/8))})})
    assert intersects(a,b)
    b=a.model_copy(update={'pose':a.pose.model_copy(update={'position_m':(a.pose.position_m[0]+a.size_m[0],1,.4)})})
    assert not intersects(a,b,1e-6)


def test_far_clipping_and_no_fake_execution(tmp_path):
    env=MinimalEnvironmentAdapter('clip',tmp_path,load_scene().model_copy(update={'width':3,'height':3,'far_m':.1}))
    obs=run(env.observe(context(env,'o')))
    assert all(v is None for row in json.loads(content(obs.artifacts[1]))['depth_m'] for v in row)
    with pytest.raises(UnsupportedCapability):
        run(env.execute_layout(None,None,(),context(env,'execute')))


def test_corrupt_depth_rejected(env):
    obs=run(env.observe(context(env,'o')))
    ref=obs.artifacts[1]
    Path(unquote(urlparse(ref.uri).path)).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='integrity'):
        run(env.get_depth(obs))


def test_rotation_changes_camera_rays_and_depth_is_not_range(env):
    from math import sin,cos,pi
    first=run(env.observe(context(env,'front')))
    pose=first.camera_pose.model_copy(update={'orientation_xyzw':(0,0,0,1)})  # now looks down
    assert run(env.move_camera(pose,context(env,'rotate'))).status=='succeeded'
    down=run(env.observe(context(env,'down')))
    depth=json.loads(content(down.artifacts[1]))['depth_m']
    assert depth[12][16]==pytest.approx(1.5)
    assert depth[12][20]==pytest.approx(1.5)  # same optical depth, larger Euclidean ray length
    assert first.artifacts[0].sha256!=down.artifacts[0].sha256


def test_layout_query_does_not_change_environment(env):
    before=run(env.observe(context(env,'before')))
    run(env.detect_collision(layout(env,env.scene.objects[1].geometry),context(env,'query')))
    after=run(env.observe(context(env,'after')))
    assert before.camera_pose==after.camera_pose
    assert before.artifacts==after.artifacts


def test_session_rejects_changed_scene(env):
    with pytest.raises(ValueError,match='another scene'):
        MinimalEnvironmentAdapter(env.task_id,env.directory.parent,env.scene.model_copy(update={'far_m':10}))
