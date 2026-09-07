"""Deterministic box diagnostics. No LLM, perception or physics-engine dependency."""
from itertools import combinations
from math import sqrt, prod
from pathlib import Path
from app.environment.geometry import axes, cross, dot, sub, corners, rotate
from app.verification.models import (SceneSnapshot, VerifierConfig, Diagnostic, DiagnosisReport,
                                      MovePrescription, fingerprint)


def load_config():
    return VerifierConfig.model_validate_json((Path(__file__).resolve().parents[2]/'configs/geometry-verifier-v1.json').read_text())


def bounds(box):
    points = corners(box)
    return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))


def aligned(box,tolerance):
    return all(max(abs(v) for v in axis) >= 1-tolerance for axis in axes(box.pose))


def penetration(a,b,tolerance):
    """SAT separating translations for A (including containment); None means no penetration."""
    aa, bb = axes(a.pose), axes(b.pose)
    delta = sub(b.pose.position_m,a.pose.position_m)
    solutions = []
    for candidate in (*aa,*bb,*(cross(x,y) for x in aa for y in bb)):
        norm = sqrt(dot(candidate,candidate))
        if norm < 1e-10:
            continue
        axis = tuple(v/norm for v in candidate)
        radius = sum(s/2*abs(dot(axis,v)) for s,v in zip(a.size_m,aa)) + sum(s/2*abs(dot(axis,v)) for s,v in zip(b.size_m,bb))
        distance = dot(delta,axis)
        depth = radius-abs(distance)
        if depth <= tolerance:
            return None
        direction = -1 if distance >= 0 else 1
        solutions.append((depth,tuple(direction*depth*v for v in axis)))
    return sorted(set(solutions))


class DeterministicGeometryVerifier:
    def __init__(self,config=None):
        self.config = config or load_config()
        self.version = 'box-verifier-v1:'+fingerprint(self.config)

    def diagnose(self,scene: SceneSnapshot) -> DiagnosisReport:
        scene = SceneSnapshot.model_validate(scene.model_dump(mode='json'))
        config = self.config
        diagnostics = []
        objects = {o.object_id:o for o in scene.objects}

        def add(rule,ids,status,reason,measurements,amount=0,suggestions=()):
            ident = rule+':'+','.join(ids)
            editable = tuple(f'{i}.position' for i in ids if i in objects and objects[i].movable)
            moves = tuple(MovePrescription(object_id=i,delta_m=delta,diagnosis_id=ident) for i,delta in suggestions if objects[i].movable)
            result = Diagnostic(diagnosis_id=ident,rule_id=rule,status=status,object_ids=ids,reason=reason,
                measurements=measurements,severity=min(1,max(0,amount)/config.severity_scale_m),editable_variables=editable,suggestions=moves)
            diagnostics.append(result)
            return result

        for a,b in combinations(sorted(scene.objects,key=lambda o:o.object_id),2):
            solutions = penetration(a.geometry,b.geometry,config.penetration_tolerance_m)
            volume = None
            if aligned(a.geometry,config.axis_tolerance) and aligned(b.geometry,config.axis_tolerance):
                al,ah = bounds(a.geometry)
                bl,bh = bounds(b.geometry)
                volume = prod(max(0,min(ah[i],bh[i])-max(al[i],bl[i])) for i in range(3))
            moves = []
            if solutions:
                for _,delta in solutions:
                    moves.extend(((a.object_id,delta),(b.object_id,tuple(-v for v in delta))))
            add('collision',(a.object_id,b.object_id),'fail' if solutions else 'pass',
                'Solid boxes interpenetrate' if solutions else 'No box penetration above tolerance',
                {'penetration_depth_m':solutions[0][0] if solutions else 0, 'intersection_volume_m3':volume,
                 'tolerance_m':config.penetration_tolerance_m},solutions[0][0] if solutions else 0,moves)

        local = {}
        for obj in sorted(scene.objects,key=lambda o:o.object_id):
            low,high = bounds(obj.geometry)
            depth = scene.floor_z_m-low[2]
            add('floor_penetration',(obj.object_id,'floor'),'fail' if depth>config.penetration_tolerance_m else 'pass',
                'Object penetrates floor' if depth>config.penetration_tolerance_m else 'Object is above floor boundary',
                {'penetration_depth_m':max(0,depth),'tolerance_m':config.penetration_tolerance_m},max(0,depth),
                ((obj.object_id,(0,0,depth)),) if depth>config.penetration_tolerance_m else ())
            if obj.anchored or not obj.requires_support:
                continue
            support = objects.get(obj.support_id)
            ids = (obj.object_id,) if obj.support_id is None else (obj.object_id,obj.support_id)
            if obj.support_id is None or (obj.support_id != 'floor' and support is None):
                local[obj.object_id] = add('support',ids,'unknown' if obj.support_id is None else 'fail',
                    'Support intent missing' if obj.support_id is None else 'Declared support object missing',{},config.severity_scale_m)
                continue
            if not aligned(obj.geometry,config.axis_tolerance) or (support and not aligned(support.geometry,config.axis_tolerance)):
                local[obj.object_id] = add('support',ids,'unknown','Rotated support geometry is outside V1 capability',{})
                continue
            com_offset = rotate(obj.center_of_mass_local_m or (0,0,0),obj.geometry.pose)
            com = tuple(x+y for x,y in zip(obj.geometry.pose.position_m,com_offset))
            top = scene.floor_z_m
            area = (high[0]-low[0])*(high[1]-low[1])
            margin = None
            dx = dy = 0.0
            if support:
                sl,sh = bounds(support.geometry)
                top = sh[2]
                intervals = [(max(low[i],sl[i]),min(high[i],sh[i])) for i in range(2)]
                area = prod(max(0,b-a) for a,b in intervals)
                margin = min(com[i]-intervals[i][0] for i in range(2))
                margin = min(margin,*(intervals[i][1]-com[i] for i in range(2)))
                dx,dy = ((sl[i]+sh[i])/2-com[i] for i in range(2))
            gap = low[2]-top
            contact = abs(gap)<=config.contact_tolerance_m and area>0
            good_margin = margin is None or margin>=config.minimum_support_margin_m
            valid = contact and good_margin
            reason = ('Contact and center-of-mass projection satisfy geometric support rules' if valid else
                      'Floating above declared support' if gap>config.contact_tolerance_m else
                      'Penetrates declared support' if gap < -config.contact_tolerance_m else
                      'Insufficient contact area or center-of-mass support margin')
            correction = (dx if not good_margin or area<=0 else 0,dy if not good_margin or area<=0 else 0,-gap)
            local[obj.object_id] = add('support',ids,'pass' if valid else 'fail',reason,
                {'gap_m':gap,'contact':contact,'contact_area_m2':area if contact else 0,
                 'projected_overlap_area_m2':area,'support_margin_m':margin,
                 'minimum_margin_m':config.minimum_support_margin_m,'contact_tolerance_m':config.contact_tolerance_m,
                 'center_of_mass_assumed':obj.center_of_mass_local_m is None},
                0 if valid else max(abs(gap),max(0,config.minimum_support_margin_m-(margin or 0))),
                ((obj.object_id,correction),) if not valid else ())

        def grounded(ident,seen):
            if ident == 'floor':
                return 'pass'
            obj = objects.get(ident)
            if obj is None:
                return 'unknown'
            if obj.anchored:
                return 'pass'
            if ident in seen or ident not in local:
                return 'unknown'
            if local[ident].status != 'pass':
                return local[ident].status
            return grounded(obj.support_id,seen|{ident})
        for obj in sorted(scene.objects,key=lambda o:o.object_id):
            if obj.object_id in local and local[obj.object_id].status == 'pass':
                status = grounded(obj.object_id,set())
                add('support_chain',(obj.object_id,),status,
                    'Support chain reaches floor or declared anchor' if status=='pass' else 'Support chain is not verified as grounded',{})
        status = 'fail' if any(d.status=='fail' for d in diagnostics) else 'unknown' if any(d.status=='unknown' for d in diagnostics) else 'pass'
        return DiagnosisReport(scene_revision=scene.revision,verifier_version=self.version,status=status,diagnostics=tuple(diagnostics),
            assumptions=('Objects are solid boxes; dimensions are meters in a common Z-up frame.',
                         'Missing center of mass assumes uniform solid box.',
                         'Support checks are geometric necessary conditions, not a dynamics certificate.'))

    async def verify(self, task, belief, layout, geometry):
        """Frozen GeometryVerifier port, bridged to a versioned scene artifact."""
        from app.verification.port import verify_layout
        return verify_layout(self,task,belief,layout,geometry)
