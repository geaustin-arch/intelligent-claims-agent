"""LangGraph workflow with an explicit interrupt for claims requiring review."""
from typing import TypedDict, Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command
from .domain import Claim, assess_claim

class State(TypedDict, total=False):
    claim: dict[str, Any]
    assessment: dict[str, Any]
    approval: dict[str, Any]
    audit: list[dict[str, Any]]
    status: str

def intake(state: State) -> State:
    c = state["claim"]
    claim = Claim(c["claim_id"], float(c["amount"]), c["category"], c["policy_id"])
    a = assess_claim(claim)
    return {"assessment": {"outcome": a.outcome, "reasons": list(a.reasons), "confidence": a.confidence},
            "audit": state.get("audit", []) + [{"event": "assessment_completed", "claim_id": c["claim_id"]}]}

def route(state: State) -> str:
    return "review" if state["assessment"]["outcome"] == "human_review" else "finish"

def review(state: State) -> State:
    decision = interrupt({"type": "claim_approval", "claim": state["claim"], "assessment": state["assessment"],
                          "message": "Review and provide {approved: bool, reviewer: str, note: str}."})
    return {"approval": decision, "audit": state.get("audit", []) + [{"event": "human_decision_recorded", "reviewer": decision.get("reviewer", "unknown")}]}

def finish(state: State) -> State:
    outcome = state["approval"].get("approved") if state.get("approval") else state["assessment"]["outcome"] == "auto_approve"
    return {"status": "approved" if outcome else "rejected",
            "audit": state.get("audit", []) + [{"event": "claim_finalized", "status": "approved" if outcome else "rejected"}]}

def build_graph():
    builder = StateGraph(State)
    builder.add_node("intake", intake)
    builder.add_node("review", review)
    builder.add_node("finish", finish)
    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", route, {"review": "review", "finish": "finish"})
    builder.add_edge("review", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=MemorySaver())

graph = build_graph()

def submit(claim: dict, thread_id: str):
    return graph.invoke({"claim": claim, "audit": []}, {"configurable": {"thread_id": thread_id}})

def resume(thread_id: str, approval: dict):
    return graph.invoke(Command(resume=approval), {"configurable": {"thread_id": thread_id}})
