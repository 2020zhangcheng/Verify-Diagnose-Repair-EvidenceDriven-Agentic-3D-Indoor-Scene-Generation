"""Persistent minimal environment model with deterministic camera geometry."""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from itertools import combinations
import fcntl
import json
import os
from pathlib import Path
import tempfile
from app.contracts.models import (Observation, ArtifactRef, CameraPose, MoveResult, GeometryEvidence,
                                  OperationStatus, CandidateLayout)
from app.environment.simulation_models import SimScene, CollisionReport
from app.environment.geometry import ray_interval, sub, inside_room, intersects, corners
from app.environment.render import render


class UnsupportedCapability(NotImplementedError):
    pass


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def atomic_write(path,data):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.tmp-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_scene(path=None):
    path=path or Path(__file__).resolve().parents[2]/'configs'/'minimal-room.json'
    return SimScene.model_validate_json(Path(path).read_text())


class MinimalEnvironmentAdapter:
    def __init__(self, task_id, storage_dir, scene=None):
        self.task_id=task_id
        self.scene=scene or load_scene()
        self.scene=SimScene.model_validate(self.scene.model_dump(mode='json'))
        self.revision='sim-v1:'+sha256(encoded(self.scene.model_dump(mode='json'))).hexdigest()
        self.directory=Path(storage_dir).resolve()/sha256(task_id.encode()).hexdigest()[:24]
        self.directory.mkdir(parents=True,exist_ok=True)
        (self.directory/'artifacts').mkdir(exist_ok=True)
        if not self._camera_safe(self.scene.initial_camera.position_m):
            raise ValueError('initial camera is outside room or inside obstacle')
        with self._locked() as state:
            pass

    def _camera_safe(self,position):
        if not inside_room(position,self.scene.room_size_m,self.scene.camera_radius_m):
            return False
        return all(not (interval and interval[0]<=0<=interval[1]) for obj in self.scene.objects
                   for interval in [ray_interval(position,(1,0,0),obj.geometry,self.scene.camera_radius_m)])

    @contextmanager
    def _locked(self):
        with (self.directory/'session.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            path=self.directory/'session.json'
            try:
                if path.exists():
                    state=json.loads(path.read_text())
                    if state['revision']!=self.revision or state['task_id']!=self.task_id:
                        raise ValueError('session belongs to another scene or task')
                else:
                    state={'revision':self.revision,'task_id':self.task_id,'pose':self.scene.initial_camera.model_dump(mode='json'),
                           'camera_view_id':f'{self.task_id}:initial-camera','token':0,'tick':0,'operations':{},'layouts':{}}
                    atomic_write(path,encoded(state))
                yield state
            finally:
                fcntl.flock(lock,fcntl.LOCK_UN)

    def _artifact(self,data,extension,media_type):
        digest=sha256(data).hexdigest()
        path=self.directory/'artifacts'/f'{digest}.{extension}'
        if not path.exists():
            atomic_write(path,data)
        elif sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('artifact integrity failure')
        return ArtifactRef(artifact_id=digest,uri=path.as_uri(),sha256=digest,media_type=media_type)

    def _perform(self,ctx,kind,args,model,operation):
        if ctx.task_id!=self.task_id or ctx.expected_environment_revision!=self.revision:
            raise ValueError('task or environment revision mismatch')
        fingerprint=sha256(encoded({'kind':kind,'args':args,'run_id':ctx.run_id})).hexdigest()
        with self._locked() as state:
            if ctx.fencing_token<state['token']:
                raise ValueError('stale fencing token')
            # Persist fencing advancement even if a request subsequently fails validation.
            state['token']=ctx.fencing_token
            atomic_write(self.directory/'session.json',encoded(state))
            old=state['operations'].get(ctx.operation_id)
            if old:
                if old['fingerprint']!=fingerprint:
                    raise ValueError('operation ID reused with different request')
                return model.model_validate(old['result'])
            result=operation(state)
            state['tick']+=1
            state['operations'][ctx.operation_id]={'fingerprint':fingerprint,'kind':kind,'result':result.model_dump(mode='json')}
            atomic_write(self.directory/'session.json',encoded(state))
            return result

    async def observe(self,ctx):
        def capture(state):
            pose=CameraPose.model_validate(state['pose'])
            rgb,depth,calibration=render(self.scene,pose)
            rgb_ref=self._artifact(rgb,'png','image/png')
            depth_ref=self._artifact(encoded(depth),'json','application/vnd.roomscout.depth+json')
            calibration_ref=self._artifact(encoded(calibration),'json','application/vnd.roomscout.calibration+json')
            return Observation(observation_id=f'{ctx.operation_id}:observation',task_id=self.task_id,
                camera_view_id=state['camera_view_id'],camera_pose=pose,
                captured_at=datetime(2026,9,7,tzinfo=timezone.utc)+timedelta(seconds=state['tick']),
                environment_revision=self.revision,source='simulation',artifacts=(rgb_ref,depth_ref,calibration_ref),
                sensor_calibration_id=calibration_ref.artifact_id,operation_id=ctx.operation_id)
        return self._perform(ctx,'observe',{},Observation,capture)

    async def move_camera(self,pose,ctx):
        pose=CameraPose.model_validate(pose.model_dump(mode='json'))
        if pose.frame_id!=self.scene.frame_id:
            raise ValueError('camera frame mismatch')
        def move(state):
            current=CameraPose.model_validate(state['pose'])
            direction=sub(pose.position_m,current.position_m)
            blocked=not self._camera_safe(pose.position_m)
            for obj in self.scene.objects:
                interval=ray_interval(current.position_m,direction,obj.geometry,self.scene.camera_radius_m)
                blocked |= interval is not None and interval[1]>=0 and interval[0]<=1
            if blocked:
                return MoveResult(operation_id=ctx.operation_id,status='failed',actual_pose=current,
                                  camera_view_id=state['camera_view_id'],environment_revision=self.revision)
            state['pose']=pose.model_dump(mode='json')
            state['camera_view_id']=f'{ctx.operation_id}:camera'
            return MoveResult(operation_id=ctx.operation_id,status='succeeded',actual_pose=pose,
                              camera_view_id=state['camera_view_id'],environment_revision=self.revision)
        return self._perform(ctx,'move_camera',pose.model_dump(mode='json'),MoveResult,move)

    async def get_depth(self,observation):
        with self._locked() as state:
            receipt=state['operations'].get(observation.operation_id)
            if not receipt or receipt['kind']!='observe' or Observation.model_validate(receipt['result'])!=observation:
                raise ValueError('observation is not from this environment session')
            ref=next(a for a in observation.artifacts if a.media_type=='application/vnd.roomscout.depth+json')
            path=self.directory/'artifacts'/f'{ref.sha256}.json'
            if sha256(path.read_bytes()).hexdigest()!=ref.sha256:
                raise ValueError('depth artifact integrity failure')
            return ref

    def _collision(self,layout):
        if layout.task_id!=self.task_id:
            raise ValueError('layout task mismatch')
        placements={p.object_id:p.target for p in layout.placements}
        objects={o.object_id:o.geometry for o in self.scene.objects}
        if len(placements)!=len(layout.placements) or placements.keys()-objects.keys():
            raise ValueError('duplicate or unknown placement object')
        if any(b.pose.frame_id!=self.scene.frame_id for b in placements.values()):
            raise ValueError('layout frame mismatch')
        objects.update(placements)
        pairs=tuple((a,b) for a,b in combinations(sorted(objects),2) if (a in placements or b in placements)
                    and intersects(objects[a],objects[b],self.scene.collision_tolerance_m))
        outside=tuple(sorted(ident for ident,box in placements.items() if any(not inside_room(p,self.scene.room_size_m,-self.scene.collision_tolerance_m) for p in corners(box))))
        report=CollisionReport(layout_id=layout.layout_id,environment_revision=self.revision,collision=bool(pairs or outside),
                               pairs=pairs,outside_room_ids=outside,tolerance_m=self.scene.collision_tolerance_m)
        return GeometryEvidence(environment_revision=self.revision,status='available',
             artifacts=(self._artifact(encoded(report.model_dump(mode='json')),'json','application/vnd.roomscout.collision+json'),))

    async def detect_collision(self,layout,ctx):
        layout=CandidateLayout.model_validate(layout.model_dump(mode='json'))
        def query(state):
            old=state['layouts'].get(layout.layout_id)
            data=layout.model_dump(mode='json')
            if old and old!=data:
                raise ValueError('layout ID reused with different contents')
            result=self._collision(layout)
            state['layouts'][layout.layout_id]=data
            return result
        return self._perform(ctx,'detect_collision',layout.model_dump(mode='json'),GeometryEvidence,query)

    async def measure_geometry(self,query,ctx):
        def measure(state):
            if query.rule_id!='collision' or query.object_ids or query.region_ids:
                return GeometryEvidence(environment_revision=self.revision,status='unsupported')
            data=state['layouts'].get(query.layout_id)
            return self._collision(CandidateLayout.model_validate(data)) if data else GeometryEvidence(environment_revision=self.revision,status='unknown')
        return self._perform(ctx,'measure_geometry',query.model_dump(mode='json'),GeometryEvidence,measure)

    async def execute_layout(self,layout,decision,verification,ctx):
        raise UnsupportedCapability('minimal simulator supports sensing and collision queries only')

    async def reconcile(self,ctx):
        if ctx.task_id!=self.task_id or ctx.expected_environment_revision!=self.revision:
            raise ValueError('task or environment revision mismatch')
        with self._locked() as state:
            if ctx.fencing_token<state['token']:
                raise ValueError('stale fencing token')
            receipt=state['operations'].get(ctx.operation_id)
            status='not_started' if receipt is None else 'failed' if receipt['result'].get('status')=='failed' else 'succeeded'
            return OperationStatus(operation_id=ctx.operation_id,status=status)
