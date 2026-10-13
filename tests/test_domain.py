from claims_agent.domain import Claim, assess_claim

def test_within_policy_auto_approves():
    a = assess_claim(Claim("c1", 120, "travel", "P-1"))
    assert a.outcome == "auto_approve" and a.confidence > .9

def test_over_limit_routes_to_human():
    a = assess_claim(Claim("c2", 700, "travel", "P-1"))
    assert a.outcome == "human_review"

def test_invalid_claim_never_auto_approves():
    a = assess_claim(Claim("c3", -2, "unknown", ""))
    assert a.outcome == "human_review" and len(a.reasons) == 3
