from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Claim:
    claim_id: str
    amount: float
    category: str
    policy_id: str

@dataclass(frozen=True)
class Assessment:
    outcome: Literal["auto_approve", "human_review", "deny"]
    reasons: tuple[str, ...]
    confidence: float

LIMITS = {"medical": 750.0, "travel": 500.0, "equipment": 300.0}

def assess_claim(claim: Claim) -> Assessment:
    reasons = []
    if claim.amount <= 0: reasons.append("amount must be positive")
    if claim.category not in LIMITS: reasons.append("category is not covered by the demo policy")
    if not claim.policy_id.strip(): reasons.append("policy identifier is missing")
    if reasons: return Assessment("human_review", tuple(reasons), .99)
    limit = LIMITS[claim.category]
    if claim.amount > limit * 2:
        return Assessment("human_review", (f"amount exceeds twice the {claim.category} limit",), .95)
    if claim.amount > limit:
        return Assessment("human_review", (f"amount exceeds the {claim.category} limit",), .92)
    return Assessment("auto_approve", (f"amount is within the {claim.category} demo limit",), .98)
