"""Compatibility bridge for the existing GeometryVerifier Protocol."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse,unquote
from app.contracts.models import VerificationResult
from app.verification.models import SceneSnapshot,canonical

MEDIA_TYPE = 'application/vnd.roomscout.geometry-scene+json'


def verify_layout(verifier,task,belief,layout,geometry):
    def result(rule,status,reason,objects=(),artifact=None,measured=None):
        ident = sha256(canonical([task.task_id,layout.layout_id,geometry.environment_revision,rule,objects,verifier.version])).hexdigest()
        return VerificationResult(verification_id=ident,task_id=task.task_id,layout_id=layout.layout_id,
            belief_ref=belief.ref,environment_revision=geometry.environment_revision,rule_id=rule,
            rule_version=verifier.version,status=status,reason=reason,involved_objects=objects,
            geometry_artifact=artifact,evidence=geometry.evidence,measured_value=measured,
            checked_at=datetime.now(timezone.utc))
    if task.task_id!=belief.task_id or layout.task_id!=task.task_id or layout.belief_ref!=belief.ref or geometry.environment_revision!=belief.environment_revision:
        return (result('input','error','Task, belief or environment scope mismatch'),)
    artifacts = [a for a in geometry.artifacts if a.media_type==MEDIA_TYPE]
    if geometry.status!='available' or len(artifacts)!=1:
        return (result('input','unknown','Exactly one versioned scene artifact is required'),)
    artifact = artifacts[0]
    try:
        uri = urlparse(artifact.uri)
        if uri.scheme!='file' or uri.netloc:
            raise ValueError('Only local scene artifacts supported')
        data = Path(unquote(uri.path)).read_bytes()
        if sha256(data).hexdigest()!=artifact.sha256:
            raise ValueError('Scene artifact hash mismatch')
        scene = SceneSnapshot.model_validate_json(data)
        if scene.revision!=geometry.environment_revision or scene.frame_id!=belief.frame_id:
            raise ValueError('Scene revision/frame mismatch')
        placements = {p.object_id:p.target for p in layout.placements}
        objects = {o.object_id:o for o in scene.objects}
        if len(placements)!=len(layout.placements) or not placements.keys()<=objects.keys():
            raise ValueError('Duplicate or missing placement object')
        for ident,target in placements.items():
            if not objects[ident].movable and target!=objects[ident].geometry:
                raise ValueError('Cannot move fixed object')
        proposed = SceneSnapshot.model_validate(scene.model_copy(update={'objects':tuple(o.model_copy(update={'geometry':placements.get(o.object_id,o.geometry)}) for o in scene.objects)}).model_dump(mode='json'))
        report = verifier.diagnose(proposed)
    except (ValueError,OSError) as exc:
        return (result('input','error',str(exc),artifact=artifact),)
    results = [result(d.rule_id,d.status,d.model_dump_json(),d.object_ids,artifact,d.status=='pass') for d in report.diagnostics]
    # Geometry validity does not imply task-specific or semantic constraints are satisfied.
    for constraint in (*task.constraints,*layout.constraints):
        results.append(result(constraint.rule_id,'unknown','Additional task constraint is outside scene diagnosis V1',constraint.object_ids,artifact))
    if not results:
        results.append(result('empty_scene','unknown','Empty scene supplies no object checks',artifact=artifact))
    return tuple(results)
