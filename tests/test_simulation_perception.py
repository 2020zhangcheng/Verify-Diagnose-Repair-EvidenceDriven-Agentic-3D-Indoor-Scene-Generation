import asyncio
import pytest
from app.environment.minimal import MinimalEnvironmentAdapter, load_scene
from app.perception.simulation import SimulationPerception
from app.contracts.models import OperationContext
from scripts.demo_simulation_belief import run


def test_multiview_belief(tmp_path):
    result = asyncio.run(run(tmp_path))
    before = {o['object_id']:o for o in result['before']['objects']}
    after = {o['object_id']:o for o in result['after']['objects']}
    assert 'radiator' not in before
    assert 'radiator' in after
    assert after['screen']['uncertainty']['observation_count'] == 2
    assert len(after['screen']['evidence']) == 2
    assert after['screen']['uncertainty']['cross_view_inconsistency'] == 0
    assert after['desk'] == before['desk']  # Out of view is not deletion or new evidence.
    assert result['replay_matches']
    for obs in result['observations']:
        for obj in obs['detected_objects']:
            assert obj['uncertainty']['visibility'] + obj['uncertainty']['occlusion_ratio'] == pytest.approx(1)
            assert obj['evidence'][0]['observation_id'] == obs['observation_id']


def setup(tmp_path):
    env = MinimalEnvironmentAdapter('test',tmp_path/'env',load_scene().model_copy(update={'width':24,'height':18}))
    ctx = OperationContext(task_id='test',run_id='test',operation_id='capture',expected_environment_revision=env.revision,fencing_token=1)
    raw = asyncio.run(env.observe(ctx))
    return raw, SimulationPerception(env.scene,tmp_path/'perception')


def test_repeat_and_raw_immutability(tmp_path):
    raw,p = setup(tmp_path)
    first = asyncio.run(p.interpret(raw))
    assert first == asyncio.run(p.interpret(raw))
    assert not raw.detected_objects
    assert first.observation_id != raw.observation_id
    with pytest.raises(ValueError,match='raw'):
        asyncio.run(p.interpret(first))


@pytest.mark.parametrize('field,value',[('environment_revision','wrong'),('source','sensor')])
def test_reject_scope(tmp_path,field,value):
    raw,p = setup(tmp_path)
    with pytest.raises(ValueError,match='epoch'):
        asyncio.run(p.interpret(raw.model_copy(update={field:value})))


def test_corrupt_artifact(tmp_path):
    from urllib.parse import unquote,urlparse
    from pathlib import Path
    raw,p = setup(tmp_path)
    Path(unquote(urlparse(raw.artifacts[0].uri).path)).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='artifact'):
        asyncio.run(p.interpret(raw))
