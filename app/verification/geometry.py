"""确定性的盒体几何校验。

这个模块只处理已经结构化的三维盒体场景，不调用大模型、不读取相机数据，
也不依赖物理引擎。它提供可重复的碰撞、地面穿透、支撑关系和支撑链诊断，
并为可移动物体生成可供修复流程尝试的移动建议。
"""
from itertools import combinations
from math import sqrt, prod
from pathlib import Path
from app.environment.geometry import axes, cross, dot, sub, corners, rotate
from app.verification.models import (SceneSnapshot, VerifierConfig, Diagnostic, DiagnosisReport,
                                      MovePrescription, fingerprint)


def load_config():
    """读取并校验默认几何校验配置。

    配置文件位于项目根目录的 ``configs/geometry-verifier-v1.json``，其中定义
    了穿透容差、接触容差、支撑边界、最大移动距离和最大迭代次数等规则参数。

    Returns:
        VerifierConfig: 已通过 Pydantic 合同校验的几何校验配置。
    """
    return VerifierConfig.model_validate_json((Path(__file__).resolve().parents[2]/'configs/geometry-verifier-v1.json').read_text())


def bounds(box):
    """计算盒体在当前坐标系下的轴对齐包围盒。

    即使输入盒体带有旋转，也会先计算八个角点，再分别取三个坐标轴上的
    最小值和最大值。返回值被碰撞体积估算和支撑面积计算使用。

    Args:
        box (Box): 要计算边界的盒体。

    Returns:
        tuple[tuple[float, float, float], tuple[float, float, float]]:
            ``(low, high)``，分别表示三个轴上的最小点和最大点。
    """
    points = corners(box)
    return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))


def aligned(box,tolerance):
    """判断盒体是否近似与世界坐标轴对齐。

    V1 的支撑面积和支撑边距计算只对轴对齐盒体提供确定性结果；旋转盒体
    仍可参与 SAT 碰撞检测，但支撑诊断会返回 ``unknown``。这个函数提供
    两类校验之间所需的能力判断。

    Args:
        box (Box): 要判断的盒体。
        tolerance (float): 允许的轴方向偏差。

    Returns:
        bool: 盒体的三个局部轴是否都近似平行于世界坐标轴。
    """
    return all(max(abs(v) for v in axis) >= 1-tolerance for axis in axes(box.pose))


def penetration(a,b,tolerance):
    """使用 SAT 检测两个盒体的穿透，并生成分离移动建议。

    函数会测试两个盒体的局部轴以及叉乘得到的分离轴。若发现两个盒体已经
    分离（或只接触到容差范围），返回 ``None``；否则返回按移动深度排序的
    ``(depth, delta)`` 列表。列表中的 ``delta`` 表示把盒体 ``a`` 移出盒体
    ``b`` 所需的最小候选位移，调用方可以同时生成移动 ``b`` 的相反位移。

    Args:
        a (Box): 第一个盒体。
        b (Box): 第二个盒体。
        tolerance (float): 判定为接触而非穿透的深度容差。

    Returns:
        list[tuple[float, tuple[float, float, float]]] | None:
            穿透时返回候选 ``(深度, 位移)``，不穿透时返回 ``None``。
    """
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
    """对盒体场景执行可重复、无外部依赖的几何诊断。

    这个校验器是当前修复流程的确定性 Critic：它不让大模型直接决定结果，
    而是检查模型或规则策略提出的每一个移动候选。它可以提供碰撞、地面
    穿透、支撑和支撑链的 ``Diagnostic``，并给出仅针对可移动对象的建议。
    """

    def __init__(self,config=None):
        """创建校验器并固定本次校验使用的配置版本。

        Args:
            config (VerifierConfig | None): 可选的自定义配置；不传时读取项目
                默认配置。

        Provides:
            ``config`` 保存规则参数，``version`` 保存配置指纹，便于事件日志
            和回放时确认诊断使用的是同一套规则。
        """
        self.config = config or load_config()
        self.version = 'box-verifier-v1:'+fingerprint(self.config)

    def diagnose(self,scene: SceneSnapshot) -> DiagnosisReport:
        """诊断一个场景，并生成结构化报告。

        诊断过程不会修改传入的场景。它会遍历物体对检查碰撞，检查每个物体
        是否穿透地面、是否接触并稳定支撑于声明的支撑物，最后递归检查支撑
        链是否能到达地面或锚定物体。对于可修复的失败项，还会生成移动建议。

        Args:
            scene (SceneSnapshot): 待检查的结构化场景快照。

        Returns:
            DiagnosisReport: 包含总状态、每条规则的诊断、测量值、假设和修复
            建议的报告。总状态为 ``fail``、``unknown`` 或 ``pass``。
        """
        scene = SceneSnapshot.model_validate(scene.model_dump(mode='json'))
        config = self.config
        diagnostics = []
        objects = {o.object_id:o for o in scene.objects}

        def add(rule,ids,status,reason,measurements,amount=0,suggestions=()):
            """把一次规则检查转换为结构化诊断并追加到报告。

            这个内部函数统一生成诊断 ID、严重程度、可编辑变量和移动建议，
            确保不同规则输出相同的数据格式。
            """
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
            # ``floor`` is a virtual support and ``None`` means that the
            # support intent is missing.  Only real object IDs belong in the
            # object lookup; keeping the optional value out of ``dict.get``
            # also makes this boundary safe for static type checkers.
            support = None
            if obj.support_id is not None and obj.support_id != 'floor':
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
            """递归判断一个物体的支撑链是否最终落到地面或锚点。

            ``seen`` 用于检测循环支撑关系；遇到缺失支撑、循环或未通过支撑
            校验时返回相应的领域状态，而不是猜测物理结果。
            """
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
        """执行冻结的 GeometryVerifier 端口适配。

        该方法把任务、信念、布局和几何证据交给端口适配层，由适配层读取
        版本化场景工件后调用本类的确定性诊断逻辑。它用于兼容更大的异步
        应用合同；直接做盒体实验时应调用 :meth:`diagnose`。

        Args:
            task: 任务规格。
            belief: 当前场景信念版本。
            layout: 待验证的候选布局。
            geometry: 带场景工件引用的几何证据。

        Returns:
            由端口适配层生成的验证结果列表。
        """
        from app.verification.port import verify_layout
        return verify_layout(self,task,belief,layout,geometry)
