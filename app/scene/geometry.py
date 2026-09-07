"""Geometry estimate comparisons only; not collision/physical verification."""
from math import acos, degrees, sqrt, dist
from app.contracts.models import Box, CameraPose


def canonical_quaternion(q):
    # q and -q are the same rotation; include the 180-degree case w=0.
    sign = next((1 if x > 0 else -1 for x in reversed(q) if abs(x) > 1e-12), 1)
    return tuple(sign*x for x in q)


def angle_degrees(a, b):
    return degrees(2*acos(min(1.0, abs(sum(x*y for x, y in zip(a, b))))))


def disagreement(a: Box, b: Box, config):
    return max(dist(a.pose.position_m, b.pose.position_m)/config.position_tolerance_m,
               max(abs(x-y) for x, y in zip(a.size_m, b.size_m))/config.size_tolerance_m,
               angle_degrees(a.pose.orientation_xyzw, b.pose.orientation_xyzw)/config.orientation_tolerance_deg)


def fuse_boxes(samples):
    """samples are (Box, weight), at most one representative per viewpoint."""
    base = samples[0][0]
    total = sum(weight for _, weight in samples)
    def mean(getter):
        return tuple(sum(getter(box)[i]*weight for box, weight in samples)/total for i in range(3))
    reference = canonical_quaternion(base.pose.orientation_xyzw)
    quaternions = []
    for box, weight in samples:
        q = box.pose.orientation_xyzw
        if sum(a*b for a, b in zip(q, reference)) < 0:
            q = tuple(-x for x in q)
        quaternions.append((q, weight))
    q = tuple(sum(q[i]*w for q, w in quaternions)/total for i in range(4))
    norm = sqrt(sum(x*x for x in q))
    orientation = canonical_quaternion(tuple(x/norm for x in q))
    return Box(pose=CameraPose(frame_id=base.pose.frame_id, position_m=mean(lambda box: box.pose.position_m),
                              orientation_xyzw=orientation), size_m=mean(lambda box: box.size_m))


def viewpoint_groups(observations, config):
    representatives, groups = [], {}
    ordered = sorted(observations, key=lambda o: (o.camera_pose.position_m,
                     canonical_quaternion(o.camera_pose.orientation_xyzw), o.observation_id))
    for obs in ordered:
        pose = obs.camera_pose
        match = next((i for i, other in enumerate(representatives)
                      if dist(pose.position_m, other.position_m) <= config.viewpoint_distance_m
                      and angle_degrees(pose.orientation_xyzw, other.orientation_xyzw) <= config.viewpoint_angle_deg), None)
        if match is None:
            match = len(representatives)
            representatives.append(pose)
        groups[obs.observation_id] = match
    return groups
