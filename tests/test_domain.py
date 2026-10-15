from claims_agent.domain import Claim, assess_claim


def test_within_policy_auto_approves():
    a = assess_claim(Claim("c1", 120, "travel", "P-1"))
    assert a.outcome == "auto_approve" and a.confidence > 0.9


def test_over_limit_routes_to_human():
    a = assess_claim(Claim("c2", 700, "travel", "P-1"))
    assert a.outcome == "human_review"


def test_invalid_claim_never_auto_approves():
    a = assess_claim(Claim("c3", -2, "unknown", ""))
    assert a.outcome == "human_review" and len(a.reasons) == 3


def test_non_finite_amount_never_auto_approves():
    for amount in (float("nan"), float("inf"), float("-inf")):
        a = assess_claim(Claim("c-nan", amount, "travel", "P-1"))
        assert a.outcome == "human_review"
        assert "finite" in a.reasons[0]


def test_langgraph_pauses_and_resumes_for_human_review():
    from claims_agent.graph import resume, submit

    result = submit(
        {
            "claim_id": "review-1",
            "amount": 700,
            "category": "travel",
            "policy_id": "P-1",
        },
        "review-1",
    )
    assert result["__interrupt__"][0].value["type"] == "claim_approval"
    final = resume(
        "review-1",
        {"approved": True, "reviewer": "qa-reviewer", "note": "receipt verified"},
    )
    assert final["status"] == "approved"
    assert any(event["event"] == "human_decision_recorded" for event in final["audit"])


def test_langgraph_auto_approves_in_policy_without_interrupt():
    from claims_agent.graph import submit

    result = submit(
        {"claim_id": "auto-1", "amount": 100, "category": "travel", "policy_id": "P-1"},
        "auto-1",
    )
    assert result["status"] == "approved"
    assert "__interrupt__" not in result
