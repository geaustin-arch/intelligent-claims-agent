"""Executed inside a disposable synthetic container by docker_e2e.py."""
import json
import time
import urllib.error
import urllib.request
import jwt

def token(subject="alice", tenant="tenant-a", permissions=None):
    now = int(time.time())
    return jwt.encode({"sub": subject, "tenant_id": tenant, "iat": now,
                       "exp": now + 300, "iss": "https://synthetic.invalid",
                       "aud": "docker-synthetic", "permissions": permissions if permissions is not None else PERMISSIONS},
                      CONFIG["private"], algorithm="RS256")

def request(path, body, expected=200, subject="alice", tenant="tenant-a", permissions=None, bearer=True, method="POST", extra=None):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + token(subject, tenant, permissions)
    headers.update(extra or {})
    req = urllib.request.Request("http://127.0.0.1:8000" + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read()
    if status != expected:
        raise RuntimeError(f"{path}: expected {expected}, got {status}")
    return json.loads(payload) if payload else None

def verify(condition, message):
    if not condition:
        raise RuntimeError(message)

PERMISSIONS = ["claims:submit", "claims:review"]
body = {"claim_id": "synthetic-claim", "amount": "700.00", "category": "travel", "policy_id": "SYNTHETIC"}
extra = {"Idempotency-Key": "synthetic-submission"}
review = {"approved": True, "review_id": "synthetic-review", "expected_version": 1, "note": "Synthetic receipt checked"}
request("/claims", body, 401, bearer=False, extra=extra)
request("/claims", body, 403, permissions=[], extra=extra)
if CONFIG["phase"] == "create":
    result = request("/claims", body, extra=extra)
    verify(result["status"] == "awaiting_human_review", "Claim did not pause")
    request("/claims/synthetic-claim/review", review, 403)
    request("/claims/synthetic-claim/review", review, 404, subject="bob", tenant="tenant-b")
else:
    result = request("/claims/synthetic-claim/review", review, subject="bob")
    verify(result["status"] == "approved", "Decision did not survive/resume")
    verify(request("/claims/synthetic-claim/review", review, subject="bob") == result, "Retry changed decision")
    changed = {**review, "approved": False}
    request("/claims/synthetic-claim/review", changed, 409, subject="bob")

print("Synthetic phase passed:", CONFIG["phase"])
