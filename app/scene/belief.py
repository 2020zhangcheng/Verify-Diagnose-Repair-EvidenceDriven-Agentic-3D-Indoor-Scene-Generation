"""Deterministic evidence fusion over immutable structured observations.

No sensor, LLM, database, memory, planner or geometry-verifier dependency.
"""
from collections import defaultdict
from itertools import combinations
from typing import Awaitable, Callable
from app.contracts.models import (
    Observation, SceneBelief, SceneObject, SceneRegion, RoomGeometry, Claim,
    EvidenceRef, Uncertainty, BeliefRef, TaskSpec,
)
from app.scene.config import FusionConfig, load_config
from app.scene.geometry import disagreement, fuse_boxes, viewpoint_groups, canonical_quaternion
from app.scene.uncertainty import score_uncertainty

ObservationLoader = Callable[[str, tuple[str, ...]], Awaitable[tuple[Observation, ...]]]


class FusionInputError(ValueError):
    """Invalid scope, missing evidence or unsupported fusion epoch."""


def evidence_for(observation):
    return EvidenceRef(observation_id=observation.observation_id, camera_view_id=observation.camera_view_id)


def collect_evidence(rows):
    refs = {}
    for obs, value in rows:
        supplied = value.evidence or (evidence_for(obs),)
        for ref in supplied:
            refs[(ref.observation_id, ref.camera_view_id, ref.artifact_id or "")] = ref
    return tuple(refs[key] for key in sorted(refs))


class InMemoryObservationArchive:
    """Immutable fixture archive for unit tests and offline replay, never Memory."""
    def __init__(self, observations=()):
        self.rows = {}
        for obs in observations:
            self.add(obs)

    def add(self, obs):
        key = (obs.task_id, obs.observation_id)
        if key in self.rows and self.rows[key] != obs:
            raise FusionInputError("observation ID reused with different contents")
        self.rows[key] = obs

    async def load(self, task_id, ids):
        try:
            return tuple(self.rows[(task_id, ident)] for ident in ids)
        except KeyError as exc:
            raise FusionInputError("missing observation history") from exc


class StructuredSceneBeliefService:
    def __init__(self, observation_loader: ObservationLoader | None = None, config: FusionConfig | None = None):
        self.observation_loader = observation_loader
        self.config = config or load_config()

    async def update(self, previous: SceneBelief | None, observations: tuple[Observation, ...], task: TaskSpec) -> SceneBelief:
        if previous and (previous.task_id != task.task_id or previous.fusion_version != self.config.version):
            raise FusionInputError("previous belief task/config mismatch; start a new epoch")
        history = ()
        if previous:
            if self.observation_loader is None:
                raise FusionInputError("incremental fusion requires immutable observation loader")
            history = await self.observation_loader(task.task_id, previous.observation_ids)
            if {o.observation_id for o in history} != set(previous.observation_ids):
                raise FusionInputError("missing or unexpected observation history")
        unique = {}
        for obs in (*history, *observations):
            # Revalidate even model_copy(update=...) inputs before trusting them.
            obs = Observation.model_validate(obs.model_dump(mode="json"))
            if obs.observation_id in unique and unique[obs.observation_id] != obs:
                raise FusionInputError("observation ID reused with different contents")
            unique[obs.observation_id] = obs
        if not unique:
            raise FusionInputError("at least one observation is required")
        ordered = tuple(sorted(unique.values(), key=lambda o: (o.captured_at, o.observation_id)))
        self._validate(ordered, task, previous)
        if previous:
            # Verify the supplied prior really came from its referenced immutable history.
            rebuilt_prior = self._assemble(tuple(sorted(history, key=lambda o: (o.captured_at, o.observation_id))), task)
            if self._content(rebuilt_prior) != self._content(previous):
                raise FusionInputError("previous belief does not match its observation history")
            if set(unique) == set(previous.observation_ids):
                return previous
        result = self._assemble(ordered, task)
        return result.model_copy(update={
            "ref": BeliefRef(belief_id=result.ref.belief_id, version=previous.ref.version+1 if previous else 1),
            "parent": previous.ref if previous else None,
        })

    @staticmethod
    def _content(belief):
        return belief.model_dump(mode="json", exclude={"ref", "parent"})

    def _validate(self, observations, task, previous):
        frames = {o.camera_pose.frame_id for o in observations}
        revisions = {o.environment_revision for o in observations}
        if len(frames) != 1 or len(revisions) != 1:
            raise FusionInputError("mixed frame or environment revision; no implicit transform")
        if previous and (previous.frame_id not in frames or previous.environment_revision not in revisions):
            raise FusionInputError("environment epoch changed")
        frame = next(iter(frames))
        camera_poses = {}
        for obs in observations:
            if obs.task_id != task.task_id:
                raise FusionInputError("cross-task observation")
            camera_key = (obs.camera_pose.position_m, canonical_quaternion(obs.camera_pose.orientation_xyzw))
            if obs.camera_view_id in camera_poses and camera_poses[obs.camera_view_id] != camera_key:
                raise FusionInputError("camera_view_id reused for a different pose")
            camera_poses[obs.camera_view_id] = camera_key
            artifact_ids = {a.artifact_id for a in obs.artifacts}
            if len(artifact_ids) != len(obs.artifacts):
                raise FusionInputError("duplicate artifact ID")
            collections = ((obs.detected_objects, "object_id"), (obs.observed_regions, "region_id"), (obs.observed_claims, "claim_id"))
            for values, id_field in collections:
                if len({getattr(v, id_field) for v in values}) != len(values):
                    raise FusionInputError("duplicate entity ID in one observation")
                for value in values:
                    for evidence in value.evidence:
                        self._validate_evidence(evidence, obs, artifact_ids)
                    box = getattr(value, "geometry", None) or getattr(value, "bounds", None)
                    if box and box.pose.frame_id != frame:
                        raise FusionInputError("entity geometry frame mismatch")
            geometry = obs.observed_room_geometry
            if geometry:
                for evidence in geometry.evidence:
                    self._validate_evidence(evidence, obs, artifact_ids)
                for box in (*geometry.walls, *geometry.floors, *geometry.windows, *geometry.doors):
                    if box.pose.frame_id != frame:
                        raise FusionInputError("room geometry frame mismatch")

    @staticmethod
    def _validate_evidence(evidence, obs, artifacts):
        if (evidence.observation_id != obs.observation_id or evidence.camera_view_id != obs.camera_view_id
                or (evidence.artifact_id is not None and evidence.artifact_id not in artifacts)):
            raise FusionInputError("evidence must refer to its hosting observation/camera/artifact")

    def _assemble(self, observations, task):
        groups = viewpoint_groups(observations, self.config)
        object_rows, region_rows, claim_rows = defaultdict(list), defaultdict(list), defaultdict(list)
        for obs in observations:
            for obj in obs.detected_objects:
                object_rows[obj.object_id].append((obs, obj))
            for region in obs.observed_regions:
                region_rows[region.region_id].append((obs, region))
            for claim in obs.observed_claims:
                claim_rows[claim.claim_id].append((obs, claim))
        overlap = object_rows.keys() & region_rows.keys()
        if overlap:
            raise FusionInputError("object and region IDs must have unambiguous subjects")
        objects = tuple(self._object(ident, rows, groups) for ident, rows in sorted(object_rows.items()))
        regions = tuple(self._region(ident, rows, groups) for ident, rows in sorted(region_rows.items()))
        region_map = {r.region_id: r for r in regions}
        claims = []
        for ident, rows in sorted(claim_rows.items()):
            if rows[0][1].subject_id not in {*object_rows, *region_rows, "room"}:
                raise FusionInputError("claim subject is not present in the scene")
            claims.append(self._claim(ident, rows, region_map))
        claimed_regions = {c.subject_id for c in claims if c.predicate == "is_empty"}
        for region in regions:
            if region.region_id not in claimed_regions:
                qualified = region.uncertainty.geometry_confidence is not None and region.uncertainty.geometry_confidence >= self.config.supported_claim_min_confidence
                status = "supported" if region.kind in ("free", "occupied") and qualified else "unobserved" if region.kind == "unobserved" else "uncertain"
                if region.kind == "uncertain" and region.uncertainty.geometry_uncertainty == 1:
                    status = "conflicted"
                claims.append(Claim(claim_id=f"region:{region.region_id}:is_empty", subject_id=region.region_id,
                    predicate="is_empty", value=True if region.kind == "free" else False if region.kind == "occupied" else None,
                    status=status,
                    confidence=region.uncertainty.geometry_confidence, evidence=region.evidence))
        if len({c.claim_id for c in claims}) != len(claims):
            raise FusionInputError("derived claim ID collision")
        # Stable identity within an environment epoch and fusion configuration.
        from hashlib import sha256
        epoch = sha256((observations[0].environment_revision + self.config.version).encode()).hexdigest()[:16]
        return SceneBelief(ref=BeliefRef(belief_id=f"{task.task_id}:belief:{epoch}", version=1), task_id=task.task_id,
                           frame_id=observations[0].camera_pose.frame_id, environment_revision=observations[0].environment_revision,
                           objects=objects, regions=regions, claims=tuple(claims), room_geometry=self._room(observations),
                           observation_ids=tuple(o.observation_id for o in observations), fusion_version=self.config.version)

    def _geometry(self, rows, groups, attribute):
        samples = [(o, v, getattr(v, attribute)) for o, v in rows if getattr(v, attribute) is not None
                   and getattr(v, "knowledge", "observed") != "unobserved"]
        if not samples:
            return None, False, 0.0
        conflicting, cross = False, 0.0
        for a, b in combinations(samples, 2):
            distance = disagreement(a[2], b[2], self.config)
            conflicting |= distance > 1
            if groups[a[0].observation_id] != groups[b[0].observation_id]:
                cross = max(cross, min(1.0, distance))
        per_view = {}
        for obs, value, box in samples:
            group = groups[obs.observation_id]
            confidence = value.uncertainty.geometry_confidence
            weight = self.config.fallback_geometry_weight if confidence is None else confidence
            # A zero-confidence estimate is retained as a hypothesis, never a zero division.
            candidate = (weight, obs.observation_id, box)
            if group not in per_view or candidate[:2] > per_view[group][:2]:
                per_view[group] = candidate
        representatives = [per_view[k] for k in sorted(per_view)]
        if conflicting or sum(r[0] for r in representatives) == 0:
            best = sorted(representatives, key=lambda r: (-r[0], r[1]))[0][2]
            return best, conflicting, cross
        return fuse_boxes([(box, weight) for weight, _, box in representatives]), conflicting, cross

    def _uncertainty(self, rows, groups, conflict, cross, region=False):
        def feature(name, reduce="mean"):
            values = defaultdict(list)
            for obs, value in rows:
                raw = getattr(value.uncertainty, name)
                if name == "geometry_confidence" and hasattr(value, "geometry") and value.geometry is None:
                    continue
                if raw is not None:
                    values[groups[obs.observation_id]].append(raw)
            if not values:
                return None
            grouped = [max(v) if name not in ("geometry_uncertainty", "occlusion_ratio") else min(v) for v in values.values()]
            return max(grouped) if reduce == "max" else min(grouped) if reduce == "min" else sum(grouped)/len(grouped)
        semantic, geometry = feature("semantic_confidence"), feature("geometry_confidence")
        geometry_uncertainty = feature("geometry_uncertainty")
        if geometry_uncertainty is None and geometry is not None:
            geometry_uncertainty = 1-geometry
        if conflict:
            semantic = min(semantic, self.config.conflict_confidence_cap) if semantic is not None else None
            geometry = min(geometry, self.config.conflict_confidence_cap) if geometry is not None else None
            geometry_uncertainty = 1.0
        elif geometry_uncertainty is not None:
            geometry_uncertainty = max(geometry_uncertainty, cross)
        counts = {groups[o.observation_id] for o, v in rows if getattr(v, "knowledge", None) != "unobserved" and getattr(v, "kind", None) != "unobserved"}
        raw_cross = feature("cross_view_inconsistency", "max")
        inconsistency = max(cross, raw_cross or 0.0) if raw_cross is not None or len(counts) >= 2 else None
        value = Uncertainty(semantic_confidence=semantic, geometry_confidence=geometry, geometry_uncertainty=geometry_uncertainty,
            visibility=feature("visibility", "max"), occlusion_ratio=feature("occlusion_ratio", "min"),
            observation_count=len(counts), cross_view_inconsistency=inconsistency,
            estimator_version=self.config.version)
        score = score_uncertainty(value, self.config.region_weights if region else self.config.object_weights, self.config)
        return value.model_copy(update={"aggregate": score})

    def _object(self, ident, rows, groups):
        box, conflict, cross = self._geometry(rows, groups, "geometry")
        classes = {v.semantic_class for _, v in rows if v.semantic_class is not None}
        semantic_conflict = len(classes) > 1
        for (a, av), (b, bv) in combinations(rows, 2):
            if av.semantic_class and bv.semantic_class and av.semantic_class != bv.semantic_class and groups[a.observation_id] != groups[b.observation_id]:
                cross = 1.0
        conflict |= semantic_conflict or any(v.knowledge == "conflicted" for _, v in rows)
        knowledge = "conflicted" if conflict else "observed" if any(v.knowledge == "observed" for _, v in rows) else "inferred" if any(v.knowledge == "inferred" for _, v in rows) else "unobserved"
        # No confidence amplification merely from counting detections.
        probabilities = [v.existence_probability for _, v in rows if v.existence_probability is not None]
        return SceneObject(object_id=ident, semantic_class=next(iter(classes)) if len(classes) == 1 else None,
                           geometry=box, knowledge=knowledge, existence_probability=min(probabilities) if probabilities else None,
                           uncertainty=self._uncertainty(rows, groups, conflict, cross), evidence=collect_evidence(rows))

    def _region(self, ident, rows, groups):
        box, conflict, cross = self._geometry(rows, groups, "bounds")
        assertions = {v.kind for _, v in rows if v.kind in ("free", "occupied")}
        conflict |= len(assertions) > 1
        if len(assertions) > 1 and len({groups[o.observation_id] for o, v in rows if v.kind in assertions}) > 1:
            cross = 1.0
        def supported(value):
            u = value.uncertainty
            return (u.geometry_confidence is not None and u.geometry_confidence >= self.config.supported_claim_min_confidence
                    and u.geometry_uncertainty is not None and u.geometry_uncertainty <= self.config.free_space_max_geometry_uncertainty
                    and u.visibility is not None and u.visibility >= self.config.free_space_min_visibility
                    and u.occlusion_ratio is not None and u.occlusion_ratio <= self.config.free_space_max_occlusion)
        if conflict:
            kind = "uncertain"
        elif any(v.kind in assertions and supported(v) for _, v in rows):
            kind = next(iter(assertions))
        elif assertions:
            kind = "uncertain"
        else:
            kinds = {v.kind for _, v in rows}
            kind = "uncertain" if "uncertain" in kinds else "occluded" if "occluded" in kinds else "unobserved"
        return SceneRegion(region_id=ident, kind=kind, bounds=box,
                           uncertainty=self._uncertainty(rows, groups, conflict, cross, region=True), evidence=collect_evidence(rows))

    def _claim(self, ident, rows, regions):
        identities = {(c.subject_id, c.predicate, c.unit) for _, c in rows}
        if len(identities) != 1:
            raise FusionInputError("claim ID changed subject, predicate or unit")
        asserted = [(o, c) for o, c in rows if c.value is not None and c.status != "unobserved"]
        outcomes = {(type(c.value).__name__, c.value, c.status == "refuted") for _, c in asserted}
        conflict = len(outcomes) > 1 or any(c.status == "conflicted" for _, c in rows)
        base = max(asserted or rows, key=lambda pair: (pair[1].confidence if pair[1].confidence is not None else -1, pair[0].observation_id))[1]
        status = "conflicted" if conflict else base.status
        value, confidence = (None if conflict else base.value), base.confidence
        if status in ("supported", "refuted") and (value is None or confidence is None or confidence < self.config.supported_claim_min_confidence):
            status = "uncertain"
        evidence = collect_evidence(rows)
        if base.predicate == "is_empty":
            region = regions.get(base.subject_id)
            if region:
                joined = {(r.observation_id, r.camera_view_id, r.artifact_id or ""): r for r in (*evidence, *region.evidence)}
                evidence = tuple(joined[key] for key in sorted(joined))
            qualified = region is not None and region.kind in ("free", "occupied") and region.uncertainty.geometry_confidence is not None and region.uncertainty.geometry_confidence >= self.config.supported_claim_min_confidence
            # A new spatial measurement can resolve an existing unknown claim even
            # when perception did not repeat that claim's semantic label.
            if qualified and not conflict and value is None:
                value, status, confidence = region.kind == "free", "supported", region.uncertainty.geometry_confidence
            elif qualified and not conflict and status in ("uncertain", "unobserved") and isinstance(value, bool) and value == (region.kind == "free"):
                status = "supported"
                confidence = max(confidence or 0, region.uncertainty.geometry_confidence)
            expected = "free" if value is True else "occupied" if value is False else None
            if status == "refuted" and isinstance(value, bool):
                expected = "occupied" if value else "free"
            if region is None or expected is None or region.kind != expected:
                if region and region.kind == "uncertain" and region.uncertainty.geometry_uncertainty == 1:
                    status, value = "conflicted", None
                elif region and region.kind in ("free", "occupied") and expected:
                    status, value = "conflicted", None
                elif status in ("supported", "refuted"):
                    status = "uncertain"
        if status == "conflicted" and confidence is not None:
            confidence = min(confidence, self.config.conflict_confidence_cap)
        return Claim(claim_id=ident, subject_id=base.subject_id, predicate=base.predicate, value=value, unit=base.unit,
                     status=status, confidence=confidence, evidence=evidence)

    def _room(self, observations):
        rows = [(o, o.observed_room_geometry) for o in observations if o.observed_room_geometry is not None]
        fields = {}
        for field in ("walls", "floors", "windows", "doors"):
            unique = {}
            for _, room in rows:
                for box in getattr(room, field):
                    box = box.model_copy(update={"pose": box.pose.model_copy(update={"orientation_xyzw": canonical_quaternion(box.pose.orientation_xyzw)})})
                    unique[box.model_dump_json()] = box
            fields[field] = tuple(unique[key] for key in sorted(unique))
        complete = any(room.complete and all(set(box.model_dump_json() for box in getattr(room, name)) == set(box.model_dump_json() for box in fields[name]) for name in fields) for _, room in rows)
        return RoomGeometry(**fields, complete=complete, evidence=collect_evidence(rows))
