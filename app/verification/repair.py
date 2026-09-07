"""Replaceable diagnosis-driven MOVE policy and bounded re-verification loop."""
from math import sqrt
from typing import Protocol
from app.verification.models import SceneSnapshot, DiagnosisReport, MovePrescription, RepairResult


class RepairPolicy(Protocol):
    def propose(self,scene:SceneSnapshot,report:DiagnosisReport) -> tuple[MovePrescription,...]: ...


class RuleRepairPolicy:
    """Deterministic baseline; does not impersonate an LLM agent."""
    def propose(self,scene,report):
        return tuple(move for diagnostic in report.diagnostics if diagnostic.status=='fail' for move in diagnostic.suggestions)


def apply_move(scene,move,report,maximum_move_m):
    if report.scene_revision!=scene.revision:
        raise ValueError('stale diagnosis')
    move = MovePrescription.model_validate(move.model_dump(mode='json'))
    diagnostic = next((d for d in report.diagnostics if d.diagnosis_id==move.diagnosis_id),None)
    if diagnostic is None or diagnostic.status!='fail' or f'{move.object_id}.position' not in diagnostic.editable_variables:
        raise ValueError('move must address editable diagnosed object')
    if sqrt(sum(v*v for v in move.delta_m))>maximum_move_m:
        raise ValueError('movement budget exceeded')
    objects = []
    found = False
    for obj in scene.objects:
        if obj.object_id==move.object_id:
            found = True
            if not obj.movable:
                raise ValueError('fixed object')
            pose = obj.geometry.pose.model_copy(update={'position_m':tuple(x+d for x,d in zip(obj.geometry.pose.position_m,move.delta_m))})
            obj = obj.model_copy(update={'geometry':obj.geometry.model_copy(update={'pose':pose})})
        objects.append(obj)
    if not found:
        raise ValueError('unknown object')
    return SceneSnapshot.model_validate(scene.model_copy(update={'objects':tuple(objects)}).model_dump(mode='json'))


def repair_scene(scene,verifier,policy=None,emit=None):
    policy = policy or RuleRepairPolicy()
    emit = emit or (lambda kind,payload: None)
    initial = scene.revision
    emit('SCENE_INPUT',scene.model_dump(mode='json'))
    actions = []
    report = verifier.diagnose(scene)
    emit('DIAGNOSIS',report.model_dump(mode='json'))
    def objective(r):
        return (sum(d.status=='fail' for d in r.diagnostics),sum(d.status=='unknown' for d in r.diagnostics),sum(d.severity for d in r.diagnostics if d.status=='fail'))
    for iteration in range(verifier.config.maximum_iterations):
        if report.status=='pass':
            return RepairResult(status='pass',initial_revision=initial,scene=scene,report=report,actions=tuple(actions),iterations=iteration)
        candidates = []
        old_bad = {d.diagnosis_id for d in report.diagnostics if d.status!='pass'}
        for move in policy.propose(scene,report):
            emit('REPAIR_PROPOSED',{'scene_revision':scene.revision,'move':move.model_dump(mode='json')})
            try:
                candidate = apply_move(scene,move,report,verifier.config.maximum_move_m)
            except ValueError as exc:
                emit('REPAIR_REJECTED',{'reason':str(exc)})
                continue
            checked = verifier.diagnose(candidate)
            emit('REVERIFICATION',checked.model_dump(mode='json'))
            bad = {d.diagnosis_id for d in checked.diagnostics if d.status!='pass'}
            if bad<=old_bad and objective(checked)<objective(report):
                candidates.append((objective(checked),sqrt(sum(v*v for v in move.delta_m)),move.model_dump_json(),move,candidate,checked))
        if not candidates:
            return RepairResult(status='blocked',initial_revision=initial,scene=scene,report=report,actions=tuple(actions),iterations=iteration)
        _,_,_,move,candidate,checked = min(candidates,key=lambda c:c[:3])
        # Durable intent precedes accepting a new scene state; caller supplies event sink.
        emit('ACTION_INTENT',{'source_revision':scene.revision,'target_revision':candidate.revision,'move':move.model_dump(mode='json')})
        scene,report = candidate,checked
        actions.append(move)
        emit('ACTION_EXECUTED',{'scene':scene.model_dump(mode='json'),'move':move.model_dump(mode='json')})
        emit('DIAGNOSIS',report.model_dump(mode='json'))
    return RepairResult(status='pass' if report.status=='pass' else 'iteration_limit',initial_revision=initial,scene=scene,report=report,actions=tuple(actions),iterations=verifier.config.maximum_iterations)
