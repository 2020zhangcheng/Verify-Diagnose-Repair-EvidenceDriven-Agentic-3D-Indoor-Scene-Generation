"""Authored structured observations for the module demo; NOT perception output."""
from datetime import timedelta
from app.contracts.models import Box, CameraPose, SceneObject, SceneRegion, Claim, RoomGeometry, Uncertainty


def box(x=0, y=0, z=.5, size=(1, 1, 1)):
    return Box(pose=CameraPose(frame_id="world", position_m=(x, y, z), orientation_xyzw=(0, 0, 0, 1)), size_m=size)


def structured_observation(base, round_number):
    """Input facts vary; no SceneBelief is created in the fixture."""
    detailed = round_number > 1
    desk = SceneObject(object_id="desk", semantic_class="desk", knowledge="observed", existence_probability=.98,
        geometry=box(x=.04 if detailed else 0, y=.5, z=.375, size=(1.2,.6,.75)),
        uncertainty=Uncertainty(estimator_version="fixture-v1", semantic_confidence=.95 if detailed else .85,
            geometry_confidence=.9 if detailed else .7, geometry_uncertainty=.1 if detailed else .3,
            visibility=.9 if detailed else .5, occlusion_ratio=.1 if detailed else .5, cross_view_inconsistency=0))
    window = SceneRegion(region_id="window-region", kind="free" if detailed else "occluded",
        bounds=box(x=1, z=1, size=(2, 1, 2)), uncertainty=Uncertainty(estimator_version="fixture-v1",
        geometry_confidence=.95 if detailed else .9, geometry_uncertainty=.05 if detailed else .1,
        visibility=.95 if detailed else .2, occlusion_ratio=.05 if detailed else .8, cross_view_inconsistency=0))
    rear = SceneRegion(region_id="behind-cabinet", kind="unobserved", bounds=box(x=3, y=3),
                       uncertainty=Uncertainty(estimator_version="fixture-v1"))
    claim = Claim(claim_id="window-space-empty", subject_id="window-region", predicate="is_empty",
                  value=True if detailed else None, status="supported" if detailed else "uncertain",
                  confidence=.95 if detailed else .4)
    return base.model_copy(update={"captured_at": base.captured_at + timedelta(seconds=round_number),
        "detected_objects": (desk,), "observed_regions": (window, rear), "observed_claims": (claim,),
        "observed_room_geometry": RoomGeometry(floors=(box(z=-.05, size=(5,5,.1)),))})
