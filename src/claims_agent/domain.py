from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal


@dataclass(frozen=True)
class Claim:
    claim_id: str
    amount: Decimal
    category: str
    policy_id: str


@dataclass(frozen=True)
class Assessment:
    outcome: Literal["auto_approve", "human_review", "deny"]
    reasons: tuple[str, ...]
    confidence: float


LIMITS = {
    "medical": Decimal("750.00"),
    "travel": Decimal("500.00"),
    "equipment": Decimal("300.00"),
}


def assess_claim(claim: Claim) -> Assessment:
    reasons = []
    try:
        amount = Decimal(str(claim.amount))
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite():
        reasons.append("amount must be a finite number")
    elif amount <= 0:
        reasons.append("amount must be positive")
    if claim.category not in LIMITS:
        reasons.append("category is not covered by the demo policy")
    if not claim.policy_id.strip():
        reasons.append("policy identifier is missing")
    if reasons:
        return Assessment("human_review", tuple(reasons), 0.99)
    limit = LIMITS[claim.category]
    if amount > limit * 2:
        return Assessment(
            "human_review", (f"amount exceeds twice the {claim.category} limit",), 0.95
        )
    if amount > limit:
        return Assessment(
            "human_review", (f"amount exceeds the {claim.category} limit",), 0.92
        )
    return Assessment(
        "auto_approve", (f"amount is within the {claim.category} demo limit",), 0.98
    )
