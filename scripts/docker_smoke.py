"""Run on the host against the default, unconfigured local container."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8012"

def check(path, expected, body=None):
    request = urllib.request.Request(BASE + path, data=body,
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read()
    if status != expected:
        raise RuntimeError(f"{path}: expected {expected}, got {status}")
    return payload

if json.loads(check("/health", 200)).get("status") != "ok":
    raise RuntimeError("Invalid health response")
for path in ("/docs", "/redoc", "/openapi.json"):
    check(path, 404)
# Missing identity configuration must fail closed before application work.
check("/claims", 503, b'{}')
print("Container health, disabled docs, and fail-closed authentication passed")
