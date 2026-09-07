import asyncio
from math import sin,cos,pi
import json
from hashlib import sha256
import pytest
from app.contracts.models import Box,CameraPose,TaskSpec,SceneBelief,BeliefRef,RoomGeometry,CandidateLayout,Placement,GeometryEvidence,ArtifactRef
from app.verification.models import GeometryObject,SceneSnapshot,MovePrescription
from app.verification.geometry import DeterministicGeometryVerifier,penetration,load_config
from app.verification.repair import repair_scene,apply_move
from app.verification.io import load_snapshot
from app.verification.port import MEDIA_TYPE


def obj(ident,x=0,y=0,z=.5,size=(1,1,1),support='floor',movable=True,rotation=(0,0,0,1),**kwargs):
    return GeometryObject(object_id=ident,geometry=Box(pose=CameraPose(frame_id='world',position_m=(x,y,z),orientation_xyzw=rotation),size_m=size),support_id=support,movable=movable,**kwargs)


def scene(*objects):
    return SceneSnapshot(scene_id='test',objects=objects)


def check(s,rule):
    return [d for d in DeterministicGeometryVerifier().diagnose(s).diagnostics if d.rule_id==rule]


def test_penetration_depth_volume_and_pair():
    d = check(scene(obj('a'),obj('b',x=.8)),'collision')[0]
    assert d.status=='fail' and d.object_ids==('a','b')
    assert d.measurements['penetration_depth_m']==pytest.approx(.2)
    assert d.measurements['intersection_volume_m3']==pytest.approx(.2)
    assert 'a.position' in d.editable_variables


@pytest.mark.parametrize('x,expected',[(1,'pass'),(1-1e-7,'pass'),(.999,'fail'),(2,'pass')])
def test_contact_and_tolerance(x,expected):
    assert check(scene(obj('a'),obj('b',x=x)),'collision')[0].status==expected


def test_containment_translation_really_separates():
    a,b=obj('a',size=(4,4,4),z=2),obj('b',z=2)
    solutions=penetration(a.geometry,b.geometry,1e-6)
    depth,delta=solutions[0]
    assert depth==pytest.approx(2.5)
    moved=a.geometry.model_copy(update={'pose':a.geometry.pose.model_copy(update={'position_m':tuple(x+y for x,y in zip(a.geometry.pose.position_m,delta))})})
    assert penetration(moved,b.geometry,1e-6) is None


def test_rotated_obb_collision_not_fake_volume():
    rotation=(0,0,sin(pi/8),cos(pi/8))
    s=scene(obj('a',rotation=rotation),obj('b',x=1.1))
    d=check(s,'collision')[0]
    assert d.status=='fail' and d.measurements['intersection_volume_m3'] is None
    assert check(s,'support')[0].status=='unknown'


def test_rotated_boxes_separate_even_overlapping_aabbs():
    rotation=(0,0,sin(pi/8),cos(pi/8))
    a=obj('a',size=(3,.1,1),rotation=rotation)
    b=obj('b',x=-.3,y=.3,size=(3,.1,1),rotation=rotation)
    assert check(scene(a,b),'collision')[0].status=='pass'


def test_floating_gap_and_repair():
    s=scene(obj('a',z=.7))
    d=check(s,'support')[0]
    assert d.measurements['gap_m']==pytest.approx(.2) and d.status=='fail'
    result=repair_scene(s,DeterministicGeometryVerifier())
    assert result.status=='pass'
    assert result.scene.objects[0].geometry.pose.position_m[2]==pytest.approx(.5)
    assert s.objects[0].geometry.pose.position_m[2]==.7


def test_bad_support_com_and_contact():
    s=scene(obj('base'),obj('vase',x=.6,z=1.25,size=(.5,.5,.5),support='base'))
    d=next(d for d in check(s,'support') if d.object_ids[0]=='vase')
    assert d.measurements['contact'] and d.measurements['support_margin_m']<0
    assert d.status=='fail'
    assert repair_scene(s,DeterministicGeometryVerifier()).status=='pass'


def test_known_com_used_instead_of_box_center():
    s=scene(obj('base'),obj('vase',x=.4,z=1.25,size=(.5,.5,.5),support='base',center_of_mass_local_m=(.2,0,0)))
    d=next(d for d in check(s,'support') if d.object_ids[0]=='vase')
    assert d.status=='fail' and not d.measurements['center_of_mass_assumed']


def test_suspended_support_chain_fails():
    s=scene(obj('base',z=1),obj('upper',z=2,support='base'))
    assert next(d for d in check(s,'support') if d.object_ids[0]=='upper').status=='pass'
    assert check(s,'support_chain')[0].status=='fail'


@pytest.mark.parametrize('support,expected',[(None,'unknown'),('missing','fail')])
def test_missing_support_not_guessed(support,expected):
    assert check(scene(obj('a',support=support)),'support')[0].status==expected


def test_fixed_floating_object_blocked():
    s=scene(obj('a',z=2,movable=False))
    result=repair_scene(s,DeterministicGeometryVerifier())
    assert result.status=='blocked' and not result.actions


def test_floor_penetration_and_tiny_contact():
    assert check(scene(obj('a',z=.4)),'floor_penetration')[0].status=='fail'
    assert DeterministicGeometryVerifier().diagnose(scene(obj('a',z=.5005))).status=='pass'


def test_stale_diagnosis_and_move_budget():
    s=scene(obj('a',z=1))
    verifier=DeterministicGeometryVerifier()
    r=verifier.diagnose(s)
    move=next(d for d in r.diagnostics if d.rule_id=='support').suggestions[0]
    with pytest.raises(ValueError,match='stale'):
        apply_move(scene(obj('b')),move,r,2)
    with pytest.raises(ValueError,match='budget'):
        apply_move(s,move,r,.1)


def test_scene_validation():
    with pytest.raises(ValueError,match='duplicate'):
        scene(obj('a'),obj('a'))
    with pytest.raises(ValueError,match='self support'):
        scene(obj('a',support='a'))


def test_demo_repair_deterministic_and_journal():
    s=load_snapshot('configs/geometry-diagnosis-demo.json')
    log=[]
    result=repair_scene(s,DeterministicGeometryVerifier(),emit=lambda t,p:log.append((t,p)))
    assert result.status=='pass' and len(result.actions)==3
    assert result==repair_scene(s,DeterministicGeometryVerifier())
    intents=[i for i,(t,p) in enumerate(log) if t=='ACTION_INTENT']
    executed=[i for i,(t,p) in enumerate(log) if t=='ACTION_EXECUTED']
    assert all(i<e for i,e in zip(intents,executed))
    for t,p in log:
        if t in ('DIAGNOSIS','REVERIFICATION'):
            assert p['verifier_version']==result.report.verifier_version


def port_inputs(tmp_path):
    s=scene(obj('a'),obj('b',x=2))
    path=tmp_path/'scene.json'
    data=s.model_dump_json().encode()
    path.write_bytes(data)
    h=sha256(data).hexdigest()
    artifact=ArtifactRef(artifact_id=h,sha256=h,uri=path.as_uri(),media_type=MEDIA_TYPE)
    belief=SceneBelief(ref=BeliefRef(belief_id='belief',version=1),task_id='task',frame_id='world',environment_revision=s.revision,room_geometry=RoomGeometry(),fusion_version='test')
    task=TaskSpec(task_id='task',objective='test',constraints=(),seed=0,config_id='test')
    layout=CandidateLayout(layout_id='layout',task_id='task',belief_ref=belief.ref,placements=(Placement(object_id='b',target=obj('b',x=.8).geometry),),constraints=(),required_assumptions=(),planner_version='test')
    return task,belief,layout,GeometryEvidence(environment_revision=s.revision,status='available',artifacts=(artifact,))


def test_frozen_port_applies_candidate_before_verifying(tmp_path):
    inputs=port_inputs(tmp_path)
    results=asyncio.run(DeterministicGeometryVerifier().verify(*inputs))
    assert any(r.rule_id=='collision' and r.status=='fail' for r in results)
    assert all(r.geometry_artifact for r in results)


def test_artifact_corruption_returns_error(tmp_path):
    inputs=port_inputs(tmp_path)
    (tmp_path/'scene.json').write_text('{}')
    results=asyncio.run(DeterministicGeometryVerifier().verify(*inputs))
    assert results[0].status=='error'


def test_offline_demo_exact_replay_and_tamper_rejection(tmp_path):
    from scripts.demo_geometry_verifier import run
    from app.verification.replay import replay_journal
    output=tmp_path/'demo'
    run('configs/geometry-diagnosis-demo.json',output)
    result=replay_journal(output/'events.jsonl')
    assert result.status=='pass' and len(result.actions)==3
    rows=[json.loads(line) for line in (output/'events.jsonl').read_text().splitlines()]
    next(r for r in rows if r['type']=='ACTION_EXECUTED')['payload']['scene']['floor_z_m']=100
    corrupted=tmp_path/'corrupted.jsonl'
    corrupted.write_text('\n'.join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError,match='scene mismatch'):
        replay_journal(corrupted)


def test_failed_durable_intent_does_not_mutate_input():
    s=scene(obj('a',z=1))
    def fail(kind,payload):
        if kind=='ACTION_INTENT':
            raise OSError('storage unavailable')
    with pytest.raises(OSError):
        repair_scene(s,DeterministicGeometryVerifier(),emit=fail)
    assert s.objects[0].geometry.pose.position_m[2]==1


def test_repair_does_not_create_new_collision():
    # Dropping onto the declared floor would enter a fixed obstacle; reject this prescription.
    s=scene(obj('a',z=2),obj('obstacle',z=.5,movable=False))
    result=repair_scene(s,DeterministicGeometryVerifier())
    assert result.status=='blocked' and result.scene==s
