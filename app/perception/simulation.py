"""Visibility-gated oracle perception for static, reproducible simulation experiments."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlparse
from pydantic import Field, model_validator
from app.contracts.models import Contract, Unit, Observation, SceneObject, Uncertainty, EvidenceRef, ArtifactRef
from app.environment.minimal import encoded, atomic_write
from app.environment.simulation_models import SimScene
from app.environment.render import calibration, room_surfaces
from app.environment.geometry import rotate, ray_interval


class SimulationPerceptionConfig(Contract):
    semantic_confidence: Unit
    geometry_confidence_floor: Unit
    geometry_confidence_ceiling: Unit
    minimum_visible_pixels: int = Field(ge=1)

    @model_validator(mode='after')
    def ordered(self):
        if self.geometry_confidence_floor > self.geometry_confidence_ceiling:
            raise ValueError('confidence floor exceeds ceiling')
        return self


def load_config():
    return SimulationPerceptionConfig.model_validate_json((Path(__file__).resolve().parents[2]/'configs/simulation-perception-v1.json').read_text())


class SimulationPerception:
    def __init__(self, scene: SimScene, storage_dir, config=None):
        self.scene = SimScene.model_validate(scene.model_dump(mode='json'))
        self.config = config or load_config()
        self.revision = 'sim-v1:' + sha256(encoded(self.scene.model_dump(mode='json'))).hexdigest()
        self.version = 'simulation-perception-v1:' + sha256(encoded(self.config.model_dump(mode='json'))).hexdigest()
        self.directory = Path(storage_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    async def interpret(self, observation: Observation) -> Observation:
        observation = Observation.model_validate(observation.model_dump(mode='json'))
        if observation.source != 'simulation' or observation.environment_revision != self.revision or observation.camera_pose.frame_id != self.scene.frame_id:
            raise ValueError('perception requires matching simulation epoch/frame')
        if observation.detected_objects or observation.observed_regions or observation.observed_claims or observation.observed_room_geometry:
            raise ValueError('expected raw immutable observation')
        # Validate locally generated sensor artifacts before using their camera metadata.
        for artifact in observation.artifacts:
            uri = urlparse(artifact.uri)
            if uri.scheme != 'file' or uri.netloc or sha256(Path(unquote(uri.path)).read_bytes()).hexdigest() != artifact.sha256:
                raise ValueError('invalid sensor artifact')
        if not observation.artifacts or observation.sensor_calibration_id not in {a.artifact_id for a in observation.artifacts}:
            raise ValueError('missing calibration artifact')
        c = calibration(self.scene)
        projected, visible = Counter(), Counter()
        surfaces = (*self.scene.objects, *room_surfaces(self.scene))
        for row in range(self.scene.height):
            for col in range(self.scene.width):
                direction = rotate(((col+.5-c['cx'])/c['fx'], -(row+.5-c['cy'])/c['fy'], -1), observation.camera_pose)
                hits = []
                for index, obj in enumerate(surfaces):
                    interval = ray_interval(observation.camera_pose.position_m, direction, obj.geometry)
                    if interval is None:
                        continue
                    hit = interval[0] if interval[0] >= self.scene.near_m else interval[1]
                    if self.scene.near_m <= hit <= self.scene.far_m:
                        hits.append((hit, index, obj.object_id))
                        projected[obj.object_id] += 1
                if hits:
                    visible[min(hits)[2]] += 1
        input_hash = sha256(encoded(observation.model_dump(mode='json'))).hexdigest()
        ident = observation.observation_id + ':perception:' + sha256((input_hash+self.version).encode()).hexdigest()
        detections = [obj for obj in self.scene.objects if visible[obj.object_id] >= self.config.minimum_visible_pixels]
        report = {'source':'visibility_gated_simulator_ground_truth', 'raw_observation_id':observation.observation_id,
                  'raw_operation_id':observation.operation_id, 'raw_observation_sha256':input_hash,
                  'camera_pose':observation.camera_pose.model_dump(mode='json'), 'environment_revision':self.revision,
                  'perception_version':self.version, 'config':self.config.model_dump(mode='json'),
                  'objects':{obj.object_id:{'visible_pixels':visible[obj.object_id], 'projected_pixels':projected[obj.object_id]} for obj in detections}}
        data = encoded(report)
        digest = sha256(data).hexdigest()
        path = self.directory / (digest+'.json')
        atomic_write(path, data)
        artifact = ArtifactRef(artifact_id=digest, uri=path.as_uri(), sha256=digest, media_type='application/vnd.roomscout.simulation-perception+json')
        evidence = (EvidenceRef(observation_id=ident, camera_view_id=observation.camera_view_id, artifact_id=digest),)
        objects = []
        for obj in detections:
            visibility = visible[obj.object_id]/projected[obj.object_id]
            confidence = self.config.geometry_confidence_floor + visibility*(self.config.geometry_confidence_ceiling-self.config.geometry_confidence_floor)
            objects.append(SceneObject(object_id=obj.object_id, semantic_class=obj.object_id, geometry=obj.geometry,
                knowledge='observed', existence_probability=1, evidence=evidence,
                uncertainty=Uncertainty(estimator_version=self.version, semantic_confidence=self.config.semantic_confidence,
                    geometry_confidence=confidence, geometry_uncertainty=1-confidence, visibility=visibility,
                    occlusion_ratio=1-visibility, observation_count=1)))
        return Observation.model_validate(observation.model_copy(update={'observation_id':ident,
            'operation_id':observation.operation_id+':perception:'+self.version.split(':')[1],
            'artifacts':(*observation.artifacts, artifact), 'detected_objects':tuple(objects)}).model_dump(mode='json'))
