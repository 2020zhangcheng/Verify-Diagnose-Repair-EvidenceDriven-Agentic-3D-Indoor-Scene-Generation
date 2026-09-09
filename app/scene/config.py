"""Immutable, hashed experiment configuration; no global mutable coefficients."""
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from app.contracts.models import Contract, Unit, Positive, NonNegative


class FeatureWeights(Contract):
    semantic: NonNegative
    geometry: NonNegative
    occlusion: NonNegative
    inconsistency: NonNegative

    @model_validator(mode="after")
    def positive_sum(self):
        if self.semantic + self.geometry + self.occlusion + self.inconsistency <= 0:
            raise ValueError("at least one uncertainty weight must be positive")
        return self


class FusionConfig(Contract):
    algorithm_version: Literal["scene-belief-v1"]
    position_tolerance_m: Positive
    size_tolerance_m: Positive
    orientation_tolerance_deg: float = Field(gt=0, le=180)
    viewpoint_distance_m: Positive
    viewpoint_angle_deg: float = Field(gt=0, le=180)
    fallback_geometry_weight: float = Field(gt=0, le=1)
    conflict_confidence_cap: Unit
    supported_claim_min_confidence: Unit
    free_space_min_visibility: Unit
    free_space_max_occlusion: Unit
    free_space_max_geometry_uncertainty: Unit
    missing_feature_risk: Unit
    object_weights: FeatureWeights
    region_weights: FeatureWeights

    @property
    def fingerprint(self):
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode()).hexdigest()

    @property
    def version(self):
        return f"{self.algorithm_version}:{self.fingerprint}"


def load_config(path=None):
    path = path or Path(__file__).resolve().parents[2] / "configs" / "scene-belief-v1.json"
    return FusionConfig.model_validate_json(Path(path).read_text())
