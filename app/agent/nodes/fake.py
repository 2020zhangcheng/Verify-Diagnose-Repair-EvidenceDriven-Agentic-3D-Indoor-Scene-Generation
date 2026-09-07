"""Scripted fixture outcomes: tests infrastructure, not research algorithms."""
import asyncio
from app.contracts.models import (
    SceneBelief, BeliefRef, RoomGeometry, Claim, EvidenceRef, CandidateLayout,
    LayoutAssumption, Placement, Box, Constraint, CriticalUnknown, PossibleOutcome,
    OutcomeEffect, CandidateView, VerificationResult, Decision, Observation, ActionResult,
    Budget, SceneRegion, Uncertainty, SceneObject, TaskSpec, OperationContext,
)
from app.environment.fake import FakeEnvironmentAdapter, pose, FAKE_TIME
from app.db.repositories.store import load_entity, entity_id

RULES = ("collision", "room_boundary", "door_clearance")


def constraints():
    return tuple(Constraint(constraint_id=r, rule_id=r, hard=True, relation="true", threshold=True, object_ids=("desk",)) for r in RULES)


class FakeNodes:
    def __init__(self, session):
        self.session = session
        self.environment = FakeEnvironmentAdapter()

    def context(self, s, op, after_action=False):
        return OperationContext(operation_id=op, task_id=s["task_id"], run_id=s["run_id"],
                                expected_environment_revision="fake-room-2" if after_action else "fake-room-1", fencing_token=1)

    def load(self, state, ident, model):
        return load_entity(self.session, state["task_id"], ident, model)

    def belief(self, state):
        ref = state["scene_belief"]
        return self.load(state, f"{ref['belief_id']}@{ref['version']}", SceneBelief)

    def understand_task(self, s, op):
        spec = TaskSpec(task_id=s["task_id"], objective=s["user_request"], constraints=constraints(), seed=s["seed"], config_id=s["config_id"])
        return {"task_spec_id": f"{s['task_id']}:spec", "stage": "understand_task"}, [("TASK_UNDERSTOOD", spec)]

    def recall_memory(self, s, op):
        return {"recalled_memory_ids": [], "stage": "recall_memory"}, []

    def observe_scene(self, s, op):
        n = s["observation_round"] + 1
        adapter = FakeEnvironmentAdapter(n, s["selected_view_id"])
        if s["selected_view_id"]:
            view = self.load(s, s["selected_view_id"], CandidateView)
            asyncio.run(adapter.move_camera(view.camera_pose, self.context(s, op)))
        obs = asyncio.run(adapter.observe(self.context(s, op)))
        if s["config_id"] == "scene-belief-v1":
            from app.scene.fixtures import structured_observation
            obs = structured_observation(obs, n)
        budget = dict(s["budget"])
        if n > 1:
            budget["remaining_views"] -= 1
            budget["remaining_travel_m"] -= 1
        return {"observation_round": n, "observation_ids": s["observation_ids"] + [obs.observation_id],
                "budget": budget, "stage": "observe_scene"}, [("OBSERVATION_CREATED", obs)]

    def update_scene_belief(self, s, op):
        if s["config_id"] == "scene-belief-v1":
            from app.scene.node import update_belief_node
            return update_belief_node(self.session, s, op)
        n = s["observation_round"]
        obs = self.load(s, s["observation_ids"][-1], Observation)
        evidence = (EvidenceRef(observation_id=obs.observation_id, camera_view_id=obs.camera_view_id),)
        belief = SceneBelief(ref=BeliefRef(belief_id=f"{s['task_id']}:belief", version=n),
            parent=BeliefRef.model_validate(s["scene_belief"]) if s["scene_belief"] else None,
            task_id=s["task_id"], frame_id="world", environment_revision="fake-room-1",
            room_geometry=RoomGeometry(),
            objects=(SceneObject(object_id="desk", semantic_class="desk", geometry=Box(pose=pose(), size_m=(1.2,.6,.75)),
                                 existence_probability=1, knowledge="observed", uncertainty=Uncertainty(estimator_version="fake-v0", geometry_confidence=.9), evidence=evidence),),
            claims=(Claim(claim_id="window-space-empty", subject_id="window-region", predicate="is_empty",
                          value=True if n > 1 else None, status="supported" if n > 1 else "uncertain",
                          confidence=.99 if n > 1 else .4, evidence=evidence),),
            regions=(SceneRegion(region_id="window-region", kind="free" if n > 1 else "occluded",
                                 bounds=Box(pose=pose(1), size_m=(2, 1, 2)),
                                 uncertainty=Uncertainty(estimator_version="fake-v0", aggregate=.01 if n > 1 else .9),
                                 evidence=evidence),),
            observation_ids=tuple(s["observation_ids"]), fusion_version="fake-v0")
        return {"scene_belief": belief.ref.model_dump(mode="json"), "candidate_layout_ids": [],
                "critical_unknown_ids": [], "candidate_view_ids": [], "selected_view_id": None,
                "verification_result_ids": [], "decision": None, "stage": "update_belief"}, [("BELIEF_UPDATED", belief)]

    def generate_candidate_layouts(self, s, op):
        belief = self.belief(s)
        layouts = []
        for i in range(3):
            layouts.append(CandidateLayout(layout_id=f"{op}:layout-{i}", task_id=s["task_id"], belief_ref=belief.ref,
                placements=(Placement(object_id="desk", target=Box(pose=pose(i), size_m=(1.2, .6, .75))),),
                constraints=constraints(), required_assumptions=(LayoutAssumption(assumption_id=f"{op}:assumption-{i}",
                    claim_id="window-space-empty", expected_value=True, confidence=belief.claims[0].confidence,
                    evidence=belief.claims[0].evidence),) if i == 0 else (),
                expected_score=.9 - .1*i, feasibility=.99 if s["observation_round"] > 1 else .5,
                planner_version="fake-v0"))
        return {"candidate_layout_ids": [l.layout_id for l in layouts], "stage": "generate_layouts"}, [("LAYOUT_GENERATED", l) for l in layouts]

    def analyze_critical_unknowns(self, s, op):
        if s["observation_round"] > 1:
            return {"critical_unknown_ids": [], "stage": "find_unknowns"}, []
        layout = self.load(s, s["candidate_layout_ids"][0], CandidateLayout)
        unknown = CriticalUnknown(unknown_id=f"{op}:unknown", belief_ref=layout.belief_ref,
            claim_id="window-space-empty", assumption_ids=(layout.required_assumptions[0].assumption_id,),
            affected_layout_ids=(layout.layout_id,), description="Fake: 窗下是否有暖气片影响书桌布局可行性",
            possible_outcomes=tuple(PossibleOutcome(outcome_id=f"outcome-{v}", value=v,
                effects=(OutcomeEffect(layout_id=layout.layout_id, feasibility="feasible" if v else "infeasible",
                                       rank=1 if v else 3, action_changes=not v),)) for v in (True, False)),
            decision_impact=.9, current_uncertainty=.9, assessment_method="rule", assessment_version="fake-v0")
        return {"critical_unknown_ids": [unknown.unknown_id], "stage": "find_unknowns"}, [("UNKNOWN_DETECTED", unknown)]

    def generate_candidate_views(self, s, op):
        views = [CandidateView(view_id=f"{op}:view-{i}", belief_ref=BeliefRef.model_validate(s["scene_belief"]),
                 camera_pose=pose(i+1), target_unknown_ids=tuple(s["critical_unknown_ids"]),
                 expected_visibility=.8, expected_information_gain=.9 if i == 0 else .2,
                 decision_relevance=.9 if i == 0 else .2, movement_cost=1, redundancy=.1,
                 reachable="yes", score=.9 if i == 0 else .2, scoring_config_id="fake-v0") for i in range(2)]
        return {"candidate_view_ids": [v.view_id for v in views], "stage": "generate_views"}, [("VIEW_CANDIDATE_GENERATED", v) for v in views]

    def select_best_view(self, s, op):
        view = self.load(s, s["candidate_view_ids"][0], CandidateView)
        decision = Decision(decision_id=f"{op}:decision", task_id=s["task_id"], belief_ref=view.belief_ref,
                            kind="observe", unknown_ids=tuple(s["critical_unknown_ids"]), rationale="fake-v0: 窗下局部视角解决布局关键未知")
        return {"selected_view_id": view.view_id, "decision": decision.model_dump(mode="json"), "stage": "select_view"}, [("VIEW_SELECTED", view), ("DECISION_RECORDED", decision)]

    def verify_layout(self, s, op):
        belief = self.belief(s)
        layout = self.load(s, s["candidate_layout_ids"][0], CandidateLayout)
        status = "pass" if belief.claims[0].status == "supported" and layout.belief_ref == belief.ref else "unknown"
        results = [VerificationResult(verification_id=f"{op}:{rule}", task_id=s["task_id"],
                   layout_id=layout.layout_id, belief_ref=belief.ref, environment_revision=belief.environment_revision,
                   rule_id=rule, rule_version="fake-v0", status=status, measured_value=True, threshold=True,
                   involved_objects=("desk",), evidence=belief.claims[0].evidence, checked_at=FAKE_TIME,
                   reason="Scripted fake check; no physical validity claim") for rule in RULES]
        decision = Decision(decision_id=f"{op}:decision", task_id=s["task_id"], belief_ref=belief.ref,
                            layout_id=layout.layout_id, kind="execute" if status == "pass" else "blocked",
                            verification_ids=tuple(v.verification_id for v in results), rationale="fake-v0 verification fixture")
        return {"verification_result_ids": [v.verification_id for v in results],
                "decision": decision.model_dump(mode="json"), "stage": "verify"}, [("VERIFICATION_PASSED" if status == "pass" else "VERIFICATION_UNKNOWN", v) for v in results] + [("DECISION_RECORDED", decision)]

    def execute(self, s, op):
        decision = Decision.model_validate(s["decision"])
        layout = self.load(s, decision.layout_id, CandidateLayout)
        checks = tuple(self.load(s, i, VerificationResult) for i in s["verification_result_ids"])
        action = asyncio.run(self.environment.execute_layout(layout, decision, checks, self.context(s, op)))
        return {"action_result_id": action.action_id, "stage": "execute"}, [("ACTION_EXECUTED", action)]

    def validate(self, s, op):
        action = self.load(s, s["action_result_id"], ActionResult)
        obs = asyncio.run(FakeEnvironmentAdapter(s["observation_round"]+1, after_action=True).observe(self.context(s, op, after_action=True)))
        if action.status != "succeeded" or obs.environment_revision != action.after_revision:
            raise ValueError("post-action validation failed")
        return {"stage": "finished", "observation_ids": s["observation_ids"] + [obs.observation_id],
                "final_result": {"mode": "fake-v0", "action_id": action.action_id, "layout_id": action.layout_id,
                                 "message": "Fake 闭环完成：新视角消除窗下未知，验证后模拟移动书桌", "validated": True,
                                 "validation_observation_id": obs.observation_id}}, [("OBSERVATION_CREATED", obs)]

    def blocked(self, s, op):
        return {"stage": "blocked", "final_result": {"mode": "fake-v0", "reason": "insufficient evidence or verification"}}, []
