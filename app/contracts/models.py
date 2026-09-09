"""Shared immutable contracts for the Geometry Critic service."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ID = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Vec3 = tuple[float, float, float]


class Contract(BaseModel):
    """Base model used by every public geometry contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1


class CameraPose(Contract):
    frame_id: ID
    position_m: Vec3
    orientation_xyzw: tuple[float, float, float, float]

    @model_validator(mode="after")
    def unit_quaternion(self) -> CameraPose:
        if abs(sum(value * value for value in self.orientation_xyzw) - 1) > 1e-5:
            raise ValueError("orientation must be a unit quaternion (xyzw)")
        return self


class Box(Contract):
    pose: CameraPose
    size_m: tuple[Positive, Positive, Positive]
