"""Module-local contracts for deterministic diagnosis and bounded JSON repairs."""
from hashlib import sha256
import json
from typing import Literal
from pydantic import Field, model_validator
from app.contracts.models import Contract, Box, Vec3, ID, NonNegative, Positive


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def fingerprint(value):
    return sha256(canonical(value.model_dump(mode='json'))).hexdigest()


class GeometryObject(Contract):
    object_id: ID
    geometry: Box
    movable: bool = True
    requires_support: bool = True
    anchored: bool = False
    support_id: ID | None = None
    center_of_mass_local_m: Vec3 | None = None


class SceneSnapshot(Contract):
    scene_id: ID
    frame_id: ID = 'world'
    floor_z_m: float = 0
    objects: tuple[GeometryObject, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def valid(self):
        ids = [o.object_id for o in self.objects]
        if len(set(ids)) != len(ids) or 'floor' in ids:
            raise ValueError('duplicate or reserved object ID')
        for obj in self.objects:
            if obj.geometry.pose.frame_id != self.frame_id:
                raise ValueError('frame mismatch')
            if obj.support_id == obj.object_id:
                raise ValueError('self support is invalid')
            if obj.anchored and obj.movable:
                raise ValueError('anchored objects cannot be movable')
            if obj.center_of_mass_local_m is not None and any(abs(x)>s/2 for x,s in zip(obj.center_of_mass_local_m,obj.geometry.size_m)):
                raise ValueError('center of mass must be inside solid box')
        return self

    @property
    def revision(self):
        return 'geometry-v1:'+fingerprint(self)


class VerifierConfig(Contract):
    penetration_tolerance_m: NonNegative
    contact_tolerance_m: NonNegative
    minimum_support_margin_m: NonNegative
    axis_tolerance: Positive
    severity_scale_m: Positive
    maximum_move_m: Positive
    maximum_iterations: int = Field(ge=1,le=100)


class MovePrescription(Contract):
    object_id: ID
    delta_m: Vec3
    diagnosis_id: ID


RuleId = Literal[
    'collision',
    'penetration',
    'floor_penetration',
    'support',
    'support_chain',
    'floating',
    'support_gap',
    'support_instability',
    'out_of_room',
    'wall_penetration',
    'door_clearance',
    'path_blocked',
    'spacing_too_small',
    'orientation',
    'upright',
    'normal_mismatch',
]


class Diagnostic(Contract):
    diagnosis_id: ID
    rule_id: RuleId
    status: Literal['pass','fail','unknown']
    object_ids: tuple[ID,...]
    reason: str
    measurements: dict[str,float|bool|None]
    severity: float = Field(ge=0,le=1)
    editable_variables: tuple[str,...] = ()
    suggestions: tuple[MovePrescription,...] = ()
    # Geometry Critic fields used by the LLM Repair Router.  The legacy
    # editable_variables/suggestions fields remain for the V1 MOVE adapter.
    editable_objects: tuple[ID,...] = ()
    locked_objects: tuple[ID,...] = ()
    allowed_repair_tools: tuple[ID,...] = ()


class DiagnosisReport(Contract):
    scene_revision: ID
    verifier_version: ID
    status: Literal['pass','fail','unknown']
    diagnostics: tuple[Diagnostic,...]
    assumptions: tuple[str,...]


class RepairResult(Contract):
    status: Literal['pass','blocked','iteration_limit']
    initial_revision: ID
    scene: SceneSnapshot
    report: DiagnosisReport
    actions: tuple[MovePrescription,...]
    iterations: int
