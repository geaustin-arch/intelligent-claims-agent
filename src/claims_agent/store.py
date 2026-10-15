"""Encrypted, restart-safe local workflow adapter with cross-process review locking.

SQLite is deliberately a single-host adapter. Registry and checkpoint files must live
on a persistent local volume; use a transactional server database for multiple hosts.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time

from cryptography.fernet import Fernet
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .graph import build_graph


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class EncryptedSerializer:
    def __init__(self, cipher):
        self.cipher = cipher
        self.inner = JsonPlusSerializer(pickle_fallback=False)

    def dumps_typed(self, value):
        kind, data = self.inner.dumps_typed(value)
        return kind, self.cipher.encrypt(data)

    def loads_typed(self, value):
        kind, data = value
        return self.inner.loads_typed((kind, self.cipher.decrypt(data)))


class SafeSqliteSaver(SqliteSaver):
    def put(self, config, checkpoint, metadata, new_versions):
        # SQLiteSaver stores metadata as plaintext JSON. Persist only orchestration
        # fields, never payloads or arbitrary caller metadata. State/writes are encrypted.
        safe = {
            key: metadata[key]
            for key in ("source", "step", "parents")
            if key in metadata
        }
        config = {
            "configurable": {
                key: value
                for key, value in config["configurable"].items()
                if key in {"thread_id", "checkpoint_ns", "checkpoint_id"}
            }
        }
        return super().put(config, checkpoint, safe, new_versions)


class ClaimStore:
    def __init__(self, directory: str, key: str):
        self.cipher = Fernet(key.encode())
        self.key = key.encode()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (
            self.directory.stat().st_mode & 0o077
            or self.directory.stat().st_uid != os.getuid()
        ):
            raise PermissionError(
                "Storage requires a private directory owned by the service identity"
            )
        self.registry_path = self.directory / "claims.sqlite3"
        self.checkpoint_path = self.directory / "checkpoints.sqlite3"
        for path in (self.registry_path, self.checkpoint_path):
            if not path.exists():
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                except FileExistsError:
                    pass
            os.chmod(path, 0o600)
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS claims (thread TEXT PRIMARY KEY, tenant TEXT NOT NULL, idem TEXT UNIQUE NOT NULL, expires REAL NOT NULL, payload BLOB NOT NULL)"
            )

    def digest(self, *parts):
        return hmac.new(
            self.key, json.dumps(parts, separators=(",", ":")).encode(), hashlib.sha256
        ).hexdigest()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.registry_path, timeout=5)
        try:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("PRAGMA journal_mode=DELETE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def workflow(self):
        conn = sqlite3.connect(self.checkpoint_path, check_same_thread=False, timeout=5)
        try:
            conn.execute("PRAGMA secure_delete=ON")
            saver = SafeSqliteSaver(conn, serde=EncryptedSerializer(self.cipher))
            saver.setup()
            # Saver setup enables WAL; local private directory protects sidecar files.
            yield build_graph(saver), saver
        finally:
            conn.close()

    def decode(self, row):
        return json.loads(self.cipher.decrypt(row[0]))

    def encode(self, payload):
        return self.cipher.encrypt(json.dumps(payload, allow_nan=False).encode())

    def create(self, tenant, actor, claim, idempotency_key):
        thread = self.digest(tenant, claim["claim_id"])
        idem = self.digest(tenant, idempotency_key)
        fingerprint = self.digest(actor, claim)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload, expires FROM claims WHERE thread=? OR idem=?",
                (thread, idem),
            ).fetchone()
            if row:
                data = self.decode(row)
                if row[1] <= time.time():
                    raise Conflict(
                        "Claim has expired; purge it before reusing its identifier"
                    )
                if data["fingerprint"] != fingerprint or data["idem"] != idem:
                    raise Conflict("Claim identifier or idempotency key already used")
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM claims WHERE tenant=?", (self.digest(tenant),)
                ).fetchone()[0]
                if total >= 1000:
                    raise Conflict("Tenant claim quota exceeded; run retention cleanup")
                data = {
                    "fingerprint": fingerprint,
                    "idem": idem,
                    "actor": actor,
                    "version": 1,
                    "result": {"claim_id": claim["claim_id"], "status": "initializing"},
                }
                # Persist a retention-owned intent before writing any checkpoint.
                # A process failure can leave an initializing claim, never an orphan
                # checkpoint without an expiry/cleanup record.
                conn.execute(
                    "INSERT INTO claims VALUES (?, ?, ?, ?, ?)",
                    (
                        thread,
                        self.digest(tenant),
                        idem,
                        time.time() + 30 * 86400,
                        self.encode(data),
                    ),
                )
                conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT payload, expires FROM claims WHERE thread=?", (thread,)
                ).fetchone()
                if not row:
                    raise Conflict("Claim was removed during initialization")
                data = self.decode(row)
                if data["fingerprint"] != fingerprint or data["idem"] != idem:
                    raise Conflict("Claim identifier or idempotency key already used")
            with self.workflow() as (graph, saver):
                config = {"configurable": {"thread_id": thread}}
                snapshot = graph.get_state(config)
                # Recover a checkpoint committed just before the registry transaction failed.
                if snapshot.values:
                    if (
                        snapshot.values.get("claim") != claim
                        or snapshot.values.get("submitted_by") != actor
                    ):
                        raise Conflict("Claim identifier already used")
                    result = (
                        graph.invoke(None, config)
                        if snapshot.next
                        and not any(t.interrupts for t in snapshot.tasks)
                        else snapshot.values
                    )
                else:
                    result = graph.invoke(
                        {"claim": claim, "submitted_by": actor, "audit": []}, config
                    )
                result = self.public_result(graph, config, result)
            data["result"] = result
            conn.execute(
                "INSERT OR REPLACE INTO claims VALUES (?, ?, ?, ?, ?)",
                (
                    thread,
                    self.digest(tenant),
                    idem,
                    row[1] if row else time.time() + 30 * 86400,
                    self.encode(data),
                ),
            )
            return {**result, "version": data["version"]}

    @staticmethod
    def public_result(graph, config, result):
        state = graph.get_state(config)
        if state.next:
            return {
                "claim_id": state.values["claim"]["claim_id"],
                "status": "awaiting_human_review",
                "assessment": state.values["assessment"],
            }
        return {
            "claim_id": state.values["claim"]["claim_id"],
            "status": state.values["status"],
            "assessment": state.values["assessment"],
            "audit": state.values["audit"],
        }

    def review(self, tenant, actor, claim_id, decision):
        thread = self.digest(tenant, claim_id)
        fingerprint = self.digest(actor, decision)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload, expires FROM claims WHERE thread=?", (thread,)
            ).fetchone()
            if not row or row[1] <= time.time():
                raise NotFound()
            data = self.decode(row)
            if data.get("review_fingerprint") == fingerprint:
                return {**data["result"], "version": data["version"]}
            if data["actor"] == actor:
                raise PermissionError("A submitter cannot review their own claim")
            if (
                data["version"] != decision["expected_version"]
                or data["result"]["status"] != "awaiting_human_review"
            ):
                raise Conflict("Claim has no pending review at the requested version")
            with self.workflow() as (graph, saver):
                config = {"configurable": {"thread_id": thread}}
                state = graph.get_state(config)
                approval = {
                    "approved": decision["approved"],
                    "reviewer": actor,
                    "note": decision["note"],
                    "review_id": decision["review_id"],
                    "fingerprint": fingerprint,
                }
                existing = state.values.get("approval")
                if existing:
                    # Crash recovery must honor the already-checkpointed decision.
                    if existing.get("fingerprint") != fingerprint:
                        raise Conflict("A different decision was already recorded")
                    result = graph.invoke(None, config) if state.next else state.values
                else:
                    if not state.next:
                        raise Conflict("Claim has no pending review")
                    result = graph.invoke(Command(resume=approval), config)
                actual = graph.get_state(config).values.get("approval", {})
                if actual.get("fingerprint") != fingerprint:
                    raise Conflict("A different decision was already recorded")
                result = self.public_result(graph, config, result)
            data.update(
                result=result,
                review_fingerprint=fingerprint,
                version=data["version"] + 1,
            )
            conn.execute(
                "UPDATE claims SET payload=? WHERE thread=?",
                (self.encode(data), thread),
            )
            return {**result, "version": data["version"]}

    def purge_expired(self, now=None):
        """Operator job: delete both encrypted checkpoints and registry rows; safe to retry."""
        cutoff = time.time() if now is None else now
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT thread FROM claims WHERE expires<=?", (cutoff,)
            ).fetchall()
            with self.workflow() as (_, saver):
                for (thread,) in rows:
                    saver.delete_thread(thread)
            conn.execute("DELETE FROM claims WHERE expires<=?", (cutoff,))
            return len(rows)
