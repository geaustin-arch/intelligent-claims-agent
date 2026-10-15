import time
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path, signing_key):
    public = (
        signing_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    monkeypatch.setenv("AUTH_PUBLIC_KEY", public)
    monkeypatch.setenv("AUTH_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("AUTH_AUDIENCE", "portfolio-api")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CLAIMS_DATA_DIR", str(tmp_path / "claims"))
    monkeypatch.setenv("KNOWLEDGE_DATA_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    from claims_agent.app import app
    from claims_agent.security import TenantLimiter

    app.state.tenant_limiter = TenantLimiter()


@pytest.fixture
def headers(signing_key):
    def make(subject="alice", tenant="tenant-a", permissions=None, **overrides):
        now = int(time.time())
        claims = {
            "sub": subject,
            "tenant_id": tenant,
            "iss": "https://issuer.example.test",
            "aud": "portfolio-api",
            "iat": now,
            "exp": now + 300,
            "permissions": permissions
            if permissions is not None
            else ["claims:submit", "claims:review"],
        }
        claims.update(overrides)
        return {
            "Authorization": "Bearer "
            + jwt.encode(claims, signing_key, algorithm="RS256")
        }

    return make
