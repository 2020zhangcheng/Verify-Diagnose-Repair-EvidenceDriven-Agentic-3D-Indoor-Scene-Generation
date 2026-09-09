"""Explainable uncertainty features; scores are not Bayesian posteriors."""
from app.contracts.models import Uncertainty
from app.scene.config import FusionConfig, FeatureWeights


def score_uncertainty(value: Uncertainty, weights: FeatureWeights, config: FusionConfig):
    features = {
        "semantic": None if value.semantic_confidence is None else 1-value.semantic_confidence,
        "geometry": value.geometry_uncertainty,
        "occlusion": value.occlusion_ratio,
        "inconsistency": value.cross_view_inconsistency,
    }
    total = 0.0
    weight_sum = 0.0
    for name, feature in features.items():
        weight = getattr(weights, name)
        total += weight * (config.missing_feature_risk if feature is None else feature)
        weight_sum += weight
    return total / weight_sum
