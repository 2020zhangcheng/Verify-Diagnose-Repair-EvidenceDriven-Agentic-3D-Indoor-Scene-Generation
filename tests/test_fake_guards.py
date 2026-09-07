import asyncio
import pytest
from app.agent.edges import need_more_evidence
from app.environment.fake import FakeEnvironmentAdapter, FAKE_TIME
from app.contracts.models import BeliefRef, CandidateLayout, Constraint, Decision, OperationContext, VerificationResult


def fixture():
    ref = BeliefRef(belief_id="b", version=2)
    constraint = Constraint(constraint_id="c", rule_id="collision", hard=True, relation="true", threshold=True)
    layout = CandidateLayout(layout_id="l", task_id="t", belief_ref=ref, placements=(), constraints=(constraint,), required_assumptions=(), planner_version="fake-v0")
    decision = Decision(decision_id="d", task_id="t", belief_ref=ref, layout_id="l", kind="execute", verification_ids=("v",), rationale="fixture")
    check = VerificationResult(verification_id="v", task_id="t", layout_id="l", belief_ref=ref, environment_revision="fake-room-1", rule_id="collision", rule_version="fake-v0", status="pass", checked_at=FAKE_TIME)
    ctx = OperationContext(operation_id="op", task_id="t", run_id="r", expected_environment_revision="fake-room-1", fencing_token=1)
    return layout, decision, check, ctx


@pytest.mark.parametrize("change", [{"status": "unknown"}, {"status": "fail"}, {"environment_revision": "stale"}, {"layout_id": "other"}, {"belief_ref": BeliefRef(belief_id="b", version=1)}])
def test_fake_execution_rejects_invalid_verification(change):
    layout, decision, check, ctx = fixture()
    with pytest.raises(ValueError):
        asyncio.run(FakeEnvironmentAdapter().execute_layout(layout, decision, (check.model_copy(update=change),), ctx))


def test_fake_execution_requires_all_rules():
    layout, decision, check, ctx = fixture()
    with pytest.raises(ValueError, match="incomplete"):
        asyncio.run(FakeEnvironmentAdapter().execute_layout(layout, decision, (), ctx))


def test_evidence_edge_stops_when_budget_empty():
    assert need_more_evidence({"critical_unknown_ids": ["u"], "budget": {"remaining_views": 0, "remaining_travel_m": 10}}) == "blocked"
    assert need_more_evidence({"critical_unknown_ids": [], "budget": {"remaining_views": 0, "remaining_travel_m": 0}}) == "verify_layout"
