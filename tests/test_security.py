import asyncio
import json
import jwt
import pytest
from fastapi.testclient import TestClient
from claims_agent.app import app
from claims_agent.security import RequestBounds, TenantLimiter

PATH = "/claims"
BODY = {"claim_id": "c", "amount": "700.00", "category": "travel"}


def test_authentication_is_required_and_fail_closed(headers, monkeypatch):
    client = TestClient(app)
    assert client.post(PATH, json=BODY).status_code == 401
    assert client.post(PATH, json=BODY, headers=headers(aud="wrong")).status_code == 401
    assert (
        client.post(
            PATH, json=BODY, headers=headers(iss="https://wrong.test")
        ).status_code
        == 401
    )
    assert client.post(PATH, json=BODY, headers=headers(exp=1)).status_code == 401
    assert (
        client.post(PATH, json=BODY, headers=headers(iat=9999999999)).status_code == 401
    )
    assert (
        client.post(PATH, json=BODY, headers=headers(permissions=[])).status_code == 403
    )
    token = jwt.encode({"sub": "attacker"}, "a" * 32, algorithm="HS256")
    assert (
        client.post(
            PATH, json=BODY, headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )
    monkeypatch.delenv("AUTH_PUBLIC_KEY")
    assert client.post(PATH, json=BODY, headers=headers()).status_code == 503
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 404


def test_body_size_and_validation_never_echo_input(headers):
    client = TestClient(app, headers=headers())
    large = client.post(
        PATH, content=b"x" * 310_000, headers={"Content-Type": "application/json"}
    )
    assert large.status_code == 413
    invalid = client.post(PATH, json={"secret": "sensitive-canary"})
    assert invalid.status_code == 422
    assert "sensitive-canary" not in invalid.text
    assert invalid.headers["cache-control"] == "no-store"
    assert (
        client.post(
            PATH, content=b"x", headers={"Content-Encoding": "gzip"}
        ).status_code
        == 415
    )


def test_streamed_body_limit_is_enforced_before_app():
    called = []
    messages = [
        {"type": "http.request", "body": b"1234", "more_body": True},
        {"type": "http.request", "body": b"5678", "more_body": False},
    ]
    output = []

    async def downstream(scope, receive, send):
        called.append(True)

    async def receive():
        return messages.pop(0)

    async def send(message):
        output.append(message)

    asyncio.run(
        RequestBounds(downstream, max_bytes=6)(
            {"type": "http", "headers": []}, receive, send
        )
    )
    assert not called
    assert output[0]["status"] == 413


def test_limiter_is_bounded_and_cannot_evict_active_tenants():
    limiter = TenantLimiter(limit=1, max_tenants=1)
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert not limiter.allow("b")
    assert len(limiter.entries) == 1


def test_tenant_rate_limit_returns_429(headers):
    app.state.tenant_limiter = TenantLimiter(limit=1)
    client = TestClient(app, headers=headers())
    client.post(PATH, json=BODY, headers={"Idempotency-Key": "test"})
    result = client.post(PATH, json=BODY, headers={"Idempotency-Key": "test"})
    assert result.status_code == 429
    assert result.headers["Retry-After"] == "60"
