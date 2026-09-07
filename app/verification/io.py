"""Small JSON scene input and append-only experiment journal."""
import json
import os
from pathlib import Path
from app.verification.models import SceneSnapshot, canonical


def load_snapshot(path):
    return parse_snapshot(json.loads(Path(path).read_text()),Path(path).stem)


def parse_snapshot(raw,scene_id='scene'):
    if isinstance(raw,list):
        raw = {'scene_id':scene_id,'objects':raw}
    raw = dict(raw)
    objects = []
    for item in raw['objects']:
        item = dict(item)
        if 'geometry' not in item:
            position = item.pop('position_m')
            size = item.pop('size_m')
            rotation = item.pop('orientation_xyzw',(0,0,0,1))
            item['geometry'] = {'pose':{'frame_id':raw.get('frame_id','world'),'position_m':position,'orientation_xyzw':rotation},'size_m':size}
        objects.append(item)
    raw['objects'] = objects
    return SceneSnapshot.model_validate(raw)


class ExperimentJournal:
    def __init__(self,path):
        self.path = Path(path)
        self.stream = self.path.open('x')
        self.sequence = 0

    def emit(self,kind,payload):
        self.sequence += 1
        self.stream.write(canonical({'sequence':self.sequence,'type':kind,'payload':payload}).decode()+'\n')
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()
