from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command
from .graph import submit, resume

app = FastAPI(title="Intelligent Claims Agent", version="0.1.0")
class ClaimIn(BaseModel):
    claim_id: str = Field(min_length=1, max_length=100)
    amount: float
    category: str
    policy_id: str = "DEMO-001"
class ReviewIn(BaseModel):
    approved: bool
    reviewer: str = Field(min_length=2, max_length=120)
    note: str = Field(default="", max_length=1000)

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/claims")
def create_claim(body: ClaimIn):
    thread = body.claim_id
    result = submit(body.model_dump(), thread)
    if "__interrupt__" in result:
        return {"claim_id": thread, "status": "awaiting_human_review", "interrupt": result["__interrupt__"][0].value}
    return {"claim_id": thread, **result}

@app.post("/claims/{claim_id}/review")
def review_claim(claim_id: str, body: ReviewIn):
    try: result = resume(claim_id, body.model_dump())
    except Exception as e: raise HTTPException(409, f"No pending review for claim: {claim_id}") from e
    return {"claim_id": claim_id, **result}
