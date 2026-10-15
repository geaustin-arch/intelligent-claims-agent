from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from claims_agent.app import app
from claims_agent.store import ClaimStore, Conflict

CLAIM = {"claim_id": "c1", "amount": "700.00", "category": "travel", "policy_id": "P1"}
REVIEW = {
    "approved": True,
    "note": "receipt checked",
    "review_id": "r1",
    "expected_version": 1,
}


def client(headers, actor="alice", tenant="tenant-a", permissions=None):
    return TestClient(
        app,
        headers={
            **headers(subject=actor, tenant=tenant, permissions=permissions),
            "Idempotency-Key": "submission-1",
        },
    )


def test_review_requires_authenticated_reviewer_and_tenant_ownership(headers):
    submitter = client(headers)
    assert (
        submitter.post("/claims", json=CLAIM).json()["status"]
        == "awaiting_human_review"
    )
    assert submitter.post("/claims/c1/review", json=REVIEW).status_code == 403
    assert (
        client(headers, "bob", "tenant-b")
        .post("/claims/c1/review", json=REVIEW)
        .status_code
        == 404
    )
    assert (
        client(headers, "bob", permissions=["claims:submit"])
        .post("/claims/c1/review", json=REVIEW)
        .status_code
        == 403
    )
    reviewer = client(headers, "bob")
    assert (
        reviewer.post(
            "/claims/c1/review", json={**REVIEW, "reviewer": "forged"}
        ).status_code
        == 422
    )
    result = reviewer.post("/claims/c1/review", json=REVIEW)
    assert result.status_code == 200
    assert result.json()["status"] == "approved"
    event = next(
        e for e in result.json()["audit"] if e["event"] == "human_decision_recorded"
    )
    assert event["reviewer"] == "bob" and event["timestamp"] and event["event_id"]


def test_idempotency_conflicts_and_exact_money(headers):
    c = client(headers)
    first = c.post("/claims", json=CLAIM)
    assert first.status_code == 200
    assert c.post("/claims", json=CLAIM).json() == first.json()
    assert c.post("/claims", json={**CLAIM, "amount": "800.00"}).status_code == 409
    assert c.post("/claims", json={**CLAIM, "claim_id": "another"}).status_code == 409
    assert c.post("/claims", json={**CLAIM, "amount": "700.001"}).status_code == 422
    assert c.post("/claims", json={**CLAIM, "amount": "NaN"}).status_code == 422
    reviewer = client(headers, "bob")
    first_review = reviewer.post("/claims/c1/review", json=REVIEW)
    assert reviewer.post("/claims/c1/review", json=REVIEW).json() == first_review.json()
    assert (
        reviewer.post(
            "/claims/c1/review", json={**REVIEW, "approved": False}
        ).status_code
        == 409
    )


def test_restart_recovery_encryption_and_retention(monkeypatch):
    directory, key = os.environ["CLAIMS_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    first = ClaimStore(directory, key)
    claim = {**CLAIM, "amount": "700.00", "policy_id": "SECRET-POLICY-CANARY"}
    first.create("secret-tenant", "alice", claim, "one")
    del first
    restarted = ClaimStore(directory, key)
    result = restarted.review("secret-tenant", "bob", "c1", REVIEW)
    assert result["status"] == "approved"
    for path in restarted.directory.glob("*.sqlite3*"):
        data = path.read_bytes()
        for canary in (
            b"SECRET-POLICY-CANARY",
            b"receipt checked",
            b"secret-tenant",
            b'"reviewer": "bob"',
        ):
            assert canary not in data
    assert restarted.purge_expired(time.time() + 31 * 86400) == 1
    with sqlite3.connect(restarted.checkpoint_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0


def test_concurrent_review_records_only_one_decision():
    directory, key = os.environ["CLAIMS_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    ClaimStore(directory, key).create("t", "alice", CLAIM, "one")

    def review(actor):
        try:
            return ClaimStore(directory, key).review(
                "t", actor, "c1", {**REVIEW, "review_id": actor}
            )["status"]
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(review, ["bob", "carol"])) == ["approved", "conflict"]


def test_checkpoint_commit_before_registry_failure_recovers(monkeypatch):
    directory, key = os.environ["CLAIMS_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    store = ClaimStore(directory, key)
    store.create("t", "alice", CLAIM, "one")
    real_encode = store.encode
    monkeypatch.setattr(
        store,
        "encode",
        lambda value: (_ for _ in ()).throw(RuntimeError("simulated process crash")),
    )
    with pytest.raises(RuntimeError):
        store.review("t", "bob", "c1", REVIEW)
    recovered = ClaimStore(directory, key)
    with pytest.raises(Conflict):
        recovered.review("t", "carol", "c1", {**REVIEW, "review_id": "other"})
    assert recovered.review("t", "bob", "c1", REVIEW)["status"] == "approved"


def test_auto_approval_decimal_boundary_and_missing_storage(headers, monkeypatch):
    c = client(headers)
    assert (
        c.post("/claims", json={**CLAIM, "amount": "500.00"}).json()["status"]
        == "approved"
    )
    assert (
        client(headers, tenant="tenant-b")
        .post("/claims", json={**CLAIM, "amount": "500.01"})
        .json()["status"]
        == "awaiting_human_review"
    )
    monkeypatch.delenv("DATA_ENCRYPTION_KEY")
    assert c.post("/claims", json=CLAIM).status_code == 503


def test_checkpoint_submission_recovers_without_replacing_actor(monkeypatch):
    directory, key = os.environ["CLAIMS_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    store = ClaimStore(directory, key)
    original_encode = store.encode
    calls = 0

    def fail_final_write(data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated crash after checkpoint")
        return original_encode(data)

    monkeypatch.setattr(store, "encode", fail_final_write)
    with pytest.raises(RuntimeError):
        store.create("t", "alice", CLAIM, "one")
    recovered = ClaimStore(directory, key)
    with pytest.raises(Conflict):
        recovered.create("t", "mallory", CLAIM, "one")
    assert (
        recovered.create("t", "alice", CLAIM, "one")["status"]
        == "awaiting_human_review"
    )


def test_workflow_can_resume_in_a_separate_process():
    import subprocess
    import sys

    directory, key = os.environ["CLAIMS_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"]
    ClaimStore(directory, key).create("t", "alice", CLAIM, "one")
    script = """import os
from claims_agent.store import ClaimStore
store = ClaimStore(os.environ["CLAIMS_DATA_DIR"], os.environ["DATA_ENCRYPTION_KEY"])
result = store.review("t", "bob", "c1", {"approved":True,"note":"checked","review_id":"r1","expected_version":1})
print(result["status"])
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert result.stdout.strip() == "approved"


def test_storage_failure_is_503_not_a_review_conflict(headers, monkeypatch):
    from claims_agent.store import ClaimStore

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(ClaimStore, "review", unavailable)
    result = client(headers, "bob").post("/claims/c1/review", json=REVIEW)
    assert result.status_code == 503
    assert "database unavailable" not in result.text
