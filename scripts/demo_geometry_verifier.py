"""Diagnose a JSON scene and optionally run bounded rule-driven MOVE repair."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from app.verification.io import load_snapshot, ExperimentJournal
from app.verification.geometry import DeterministicGeometryVerifier
from app.verification.models import VerifierConfig
from app.verification.repair import repair_scene


def run(input_path,output,repair=True,config=None):
    scene = load_snapshot(input_path)
    verifier = DeterministicGeometryVerifier(config)
    output = Path(output)
    output.mkdir(parents=True,exist_ok=False)
    journal = ExperimentJournal(output/'events.jsonl')
    try:
        journal.emit('CONFIG',{'config':verifier.config.model_dump(mode='json'),'version':verifier.version,'policy':'rule-repair-v1'})
        report = verifier.diagnose(scene)
        (output/'diagnosis.json').write_text(report.model_dump_json(indent=2))
        lines = ['# Geometry diagnosis', '', f'Status: {report.status}', '', f'Scene revision: {report.scene_revision}', '']
        for d in report.diagnostics:
            if d.status != 'pass':
                lines.extend([f'## {d.rule_id}: {", ".join(d.object_ids)} ({d.status})', '', d.reason, '', '```json', json.dumps(d.measurements,indent=2), '```', ''])
        lines.extend(['## Scope', '', *report.assumptions])
        (output/'diagnosis.md').write_text('\n'.join(lines))
        if repair:
            result = repair_scene(scene,verifier,emit=journal.emit)
            journal.emit('FINAL_RESULT',result.model_dump(mode='json'))
            (output/'repaired-scene.json').write_text(result.scene.model_dump_json(indent=2))
            (output/'result.json').write_text(result.model_dump_json(indent=2))
            summary = {'before':report.status,'after':result.report.status,'status':result.status,'accepted_moves':len(result.actions)}
        else:
            journal.emit('SCENE_INPUT',scene.model_dump(mode='json'))
            journal.emit('DIAGNOSIS',report.model_dump(mode='json'))
            summary = {'status':report.status}
        summary['output'] = str(output.resolve())
        print(json.dumps(summary,indent=2))
        return summary
    finally:
        journal.close()


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input',default='configs/geometry-diagnosis-demo.json')
    parser.add_argument('--output',default=None)
    parser.add_argument('--diagnose-only',action='store_true')
    parser.add_argument('--config')
    args = parser.parse_args()
    config = VerifierConfig.model_validate_json(Path(args.config).read_text()) if args.config else None
    run(args.input,args.output or f'outputs/geometry-demo/{uuid4().hex}',not args.diagnose_only,config)
