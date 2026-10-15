"""Fail-closed JWT authorization and bounded ASGI request handling.

Trust only locally configured RS256 verification keys; never follow token-supplied URLs.
Rate and concurrency limits are per process; enforce fleet quotas at the gateway too.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import os
import time
from collections import OrderedDict
from threading import Lock

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException, Request
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    permissions: frozenset[str]


def authenticate(request: Request) -> Principal:
    issuer = os.getenv("AUTH_ISSUER")
    audience = os.getenv("AUTH_AUDIENCE")
    pem = os.getenv("AUTH_PUBLIC_KEY")
    if not all((issuer, audience, pem)):
        raise HTTPException(503, "Authentication is not configured")
    try:
        key = load_pem_public_key(pem.encode())
        if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
            raise ValueError("Require an RSA public key of at least 2048 bits")
    except (ValueError, TypeError):
        raise HTTPException(503, "Authentication is not configured") from None
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 8192:
        raise HTTPException(
            401,
            "A valid bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "sub", "tenant_id", "iss", "aud"]},
        )
        subject, tenant = claims["sub"], claims["tenant_id"]
        permissions = claims.get("permissions", [])
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or len(subject) > 200
            or not isinstance(tenant, str)
            or not tenant.strip()
            or len(tenant) > 120
            or not isinstance(permissions, list)
            or len(permissions) > 100
            or any(not isinstance(p, str) or len(p) > 100 for p in permissions)
        ):
            raise ValueError("Invalid identity claims")
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(
            401,
            "A valid bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return Principal(subject, tenant, frozenset(permissions))


def require(permission: str):
    def authorize(
        request: Request, principal: Principal = Depends(authenticate)
    ) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(403, "Permission denied")
        limiter = request.app.state.tenant_limiter
        if not limiter.allow(principal.tenant):
            raise HTTPException(
                429, "Tenant request quota exceeded", headers={"Retry-After": "60"}
            )
        return principal

    return authorize


class TenantLimiter:
    def __init__(self, limit: int = 60, max_tenants: int = 10_000):
        self.limit, self.max_tenants = limit, max_tenants
        self.entries: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self.lock = Lock()

    def allow(self, tenant: str) -> bool:
        now = time.monotonic()
        with self.lock:
            # Expire old windows; do not evict active windows to bypass quotas.
            while self.entries and next(iter(self.entries.values()))[0] <= now:
                self.entries.popitem(last=False)
            end, count = self.entries.get(tenant, (now + 60, 0))
            if tenant not in self.entries and len(self.entries) >= self.max_tenants:
                return False
            if count >= self.limit:
                return False
            self.entries[tenant] = (end, count + 1)
            return True


class RequestBounds:
    """Cap bytes before JSON parsing and reject saturation without queueing bodies."""

    def __init__(self, app, max_bytes: int = 300_000, max_concurrent: int = 16):
        self.app, self.max_bytes, self.max_concurrent = app, max_bytes, max_concurrent
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-store"),
                    ]
                )
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(
                scope, receive, secured_send
            )

        if self.active >= self.max_concurrent:
            return await reject(503, "Service is busy")
        self.active += 1
        try:
            headers = dict(scope.get("headers", []))
            if headers.get(b"content-encoding", b"identity").lower() != b"identity":
                return await reject(415, "Encoded request bodies are not supported")
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    declared = int(length)
                    if declared < 0:
                        raise ValueError()
                except ValueError:
                    return await reject(400, "Invalid Content-Length")
                if declared > self.max_bytes:
                    return await reject(413, "Request body is too large")
            body = bytearray()
            deadline = time.monotonic() + 10
            while True:
                try:
                    message = await asyncio.wait_for(
                        receive(), timeout=max(0, deadline - time.monotonic())
                    )
                except TimeoutError:
                    return await reject(408, "Request body timed out")
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.max_bytes:
                    return await reject(413, "Request body is too large")
                if not message.get("more_body", False):
                    break
            supplied = False

            async def replay():
                nonlocal supplied
                if not supplied:
                    supplied = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            await self.app(scope, replay, secured_send)
        finally:
            self.active -= 1


def secure_app(app, max_bytes: int = 300_000):
    from fastapi.exceptions import RequestValidationError

    app.state.tenant_limiter = TenantLimiter()
    app.add_middleware(RequestBounds, max_bytes=max_bytes)

    # Pydantic's default error payload can echo submitted secrets and source text.
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})
