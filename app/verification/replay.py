"""Recompute an offline diagnosis/repair journal without a model or live scene."""
import json
from pathlib import Path
from app.verification.models import SceneSnapshot, MovePrescription, VerifierConfig, RepairResult
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.repair import apply_move


def replay_journal(path):
    # The tool-routing graph uses RepairAction/TOOL_SELECTED records instead
    # of the legacy MOVE proposal contract.  Keep the original replay format
    # stable while dispatching the new journal to its deterministic replayer.
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    if any(row.get('type') == 'TOOL_SELECTED' for row in rows):
        from app.repair.replay import replay_graph_journal
        return replay_graph_journal(path)
    verifier=scene=report=proposal=intent=None
    actions=[]
    final=None
    for sequence,line in enumerate(Path(path).read_text().splitlines(),1):
        row=json.loads(line)
        if row['sequence']!=sequence:
            raise ValueError('journal sequence gap')
        kind,payload=row['type'],row['payload']
        if kind=='CONFIG':
            verifier=DeterministicGeometryVerifier(VerifierConfig.model_validate(payload['config']))
            if verifier.version!=payload['version']:
                raise ValueError('configuration hash mismatch')
        elif kind=='SCENE_INPUT':
            scene=SceneSnapshot.model_validate(payload)
            report=verifier.diagnose(scene)
        elif kind=='DIAGNOSIS':
            if verifier.diagnose(scene).model_dump(mode='json')!=payload:
                raise ValueError('diagnosis replay mismatch')
            report=verifier.diagnose(scene)
        elif kind=='REPAIR_PROPOSED':
            if scene.revision!=payload['scene_revision']:
                raise ValueError('stale proposal')
            proposal=MovePrescription.model_validate(payload['move'])
        elif kind=='REVERIFICATION':
            candidate=apply_move(scene,proposal,report,verifier.config.maximum_move_m)
            if verifier.diagnose(candidate).model_dump(mode='json')!=payload:
                raise ValueError('candidate replay mismatch')
        elif kind=='ACTION_INTENT':
            if payload['source_revision']!=scene.revision:
                raise ValueError('stale action')
            intent=MovePrescription.model_validate(payload['move'])
            candidate=apply_move(scene,intent,report,verifier.config.maximum_move_m)
            if candidate.revision!=payload['target_revision']:
                raise ValueError('target revision mismatch')
        elif kind=='ACTION_EXECUTED':
            if intent is None or intent.model_dump(mode='json')!=payload['move']:
                raise ValueError('action missing durable intent')
            scene=apply_move(scene,intent,report,verifier.config.maximum_move_m)
            if scene.model_dump(mode='json')!=payload['scene']:
                raise ValueError('executed scene mismatch')
            actions.append(intent)
            intent=None
            report=verifier.diagnose(scene)
        elif kind=='FINAL_RESULT':
            final=RepairResult.model_validate(payload)
            if final.scene!=scene or final.report!=report or final.actions!=tuple(actions):
                raise ValueError('final replay mismatch')
    if final is None:
        raise ValueError('journal has no completed repair result')
    return final
