from decimal import Decimal
import os
import sqlite3

from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal
from .security import Principal, require, secure_app
from .store import ClaimStore, Conflict, NotFound

app = FastAPI(
    title="Intelligent Claims Agent",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
secure_app(app, max_bytes=16_000)


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$", max_length=100)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    category: Literal["medical", "travel", "equipment"]
    policy_id: str = Field(default="DEMO-001", min_length=1, max_length=100)
    currency: Literal["USD"] = "USD"

    @field_validator("claim_id", "policy_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(strict=True)
    note: str = Field(min_length=1, max_length=1000)
    review_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1, strict=True)

    @field_validator("note", "review_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def store():
    directory, key = os.getenv("CLAIMS_DATA_DIR"), os.getenv("DATA_ENCRYPTION_KEY")
    if not directory or not key:
        raise HTTPException(503, "Encrypted storage is not configured")
    try:
        return ClaimStore(directory, key)
    except (ValueError, OSError):
        raise HTTPException(503, "Encrypted storage is unavailable") from None


@app.get("/health")
def health():
    return {"status": "ok"}


def operation(call):
    try:
        return call()
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except NotFound:
        raise HTTPException(404, "Claim not found") from None
    except PermissionError:
        raise HTTPException(403, "A submitter cannot review their own claim") from None
    except (sqlite3.Error, InvalidToken, ValueError, OSError):
        raise HTTPException(503, "Claim storage is unavailable") from None


@app.post("/claims")
def create_claim(
    body: ClaimIn,
    principal: Principal = Depends(require("claims:submit")),
    idempotency_key: str = Header(min_length=1, max_length=128),
    db: ClaimStore = Depends(store),
):
    if not idempotency_key.strip():
        raise HTTPException(422, "Invalid idempotency key")
    return operation(
        lambda: db.create(
            principal.tenant,
            principal.subject,
            body.model_dump(mode="json"),
            idempotency_key,
        )
    )


@app.post("/claims/{claim_id}/review")
def review_claim(
    claim_id: str,
    body: ReviewIn,
    principal: Principal = Depends(require("claims:review")),
    db: ClaimStore = Depends(store),
):
    return operation(
        lambda: db.review(
            principal.tenant, principal.subject, claim_id, body.model_dump()
        )
    )
