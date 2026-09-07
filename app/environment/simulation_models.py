"""Backend-local schemas. Public RoomScout contracts remain unchanged."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.contracts.models import Contract, Box, CameraPose, ID, Positive, NonNegative


class SimObject(Contract):
    object_id: ID
    geometry: Box
    color: tuple[Annotated[int, Field(ge=0, le=255)], Annotated[int, Field(ge=0, le=255)], Annotated[int, Field(ge=0, le=255)]]


class SimScene(Contract):
    scene_id: ID
    frame_id: ID = "world"
    room_size_m: tuple[Positive, Positive, Positive]
    objects: tuple[SimObject, ...]
    initial_camera: CameraPose
    width: int = Field(default=96, ge=1, le=512)
    height: int = Field(default=72, ge=1, le=512)
    horizontal_fov_deg: float = Field(default=70, gt=0, lt=179)
    near_m: Positive = .05
    far_m: Positive = 20
    camera_radius_m: NonNegative = .05
    collision_tolerance_m: NonNegative = .000001

    @model_validator(mode="after")
    def consistent(self):
        if self.near_m >= self.far_m:
            raise ValueError("near must be less than far")
        ids = [o.object_id for o in self.objects]
        if len(set(ids)) != len(ids) or any(i.startswith("room:") for i in ids):
            raise ValueError("duplicate or reserved object ID")
        if self.initial_camera.frame_id != self.frame_id or any(o.geometry.pose.frame_id != self.frame_id for o in self.objects):
            raise ValueError("scene frame mismatch")
        return self


class CollisionReport(Contract):
    layout_id: ID
    environment_revision: ID
    geometry_source: Literal["simulator_ground_truth"] = "simulator_ground_truth"
    collision: bool
    pairs: tuple[tuple[ID, ID], ...]
    outside_room_ids: tuple[ID, ...]
    tolerance_m: NonNegative
