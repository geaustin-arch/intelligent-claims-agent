# Intelligent Claims Agent

A human-governed LangGraph claims workflow with authenticated reviewers,
encrypted durable checkpoints, and retry-safe decisions. Demo policy limits are
fictional; approval does not trigger payment or represent insurer coverage.

## What it demonstrates

- Deterministic, decimal-based USD policy rules; invalid input is rejected.
- LangGraph interrupt/resume with encrypted SQLite state and intermediate writes.
- RS256 tokens with exact permissions and tenant-scoped workflow identifiers.
- Reviewer identity comes from the verified token. Submitters cannot self-review.
- Unique submissions, version-checked reviews, idempotent retries, and serialized
  cross-process decisions. Checkpoint-first crash recovery preserves the recorded decision.
- Server-generated audit event IDs/timestamps, decision notes, and demo policy version.
- Request limits, encrypted-at-rest business payloads, retention, and security CI.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install --no-deps -e .
pytest -q
uvicorn claims_agent.app:app --host 127.0.0.1
```

Configure `AUTH_ISSUER`, `AUTH_AUDIENCE`, and `AUTH_PUBLIC_KEY` from your identity
provider. Tokens require `sub`, `tenant_id`, `iat`, `exp`, `iss`, `aud`, and a
`permissions` list. No anonymous demo bypass exists; `/health` remains public.
Interactive docs are disabled. See [SECURITY.md](SECURITY.md) for deployment boundaries.

Set `CLAIMS_DATA_DIR` to a private persistent local directory and inject a stable
`DATA_ENCRYPTION_KEY`. A new Fernet key can be generated with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
Store that value in a secret manager; do not regenerate it on each restart.

## API

Use an identity-provider access token in `Authorization: Bearer <token>`.
`claims:submit` permits intake; `claims:review` permits review. Both are scoped to
the verified `tenant_id`. Claim IDs contain letters, numbers, dots, underscores,
and hyphens (up to 100 characters).

`POST /claims` requires `Idempotency-Key: <unique submission key>`:

```json
{"claim_id":"claim-001","amount":"700.00","category":"travel","policy_id":"DEMO-001","currency":"USD"}
```

The response includes `status` and `version`. Out-of-limit claims pause with
`awaiting_human_review`. A different authorized person submits
`POST /claims/claim-001/review`:

```json
{"approved":true,"review_id":"review-001","expected_version":1,"note":"Receipt and policy exception verified"}
```

There is no caller-controlled `reviewer` field. Exact retries return the same
result; conflicting keys, changed payloads, stale versions, and second decisions
return 409. Another tenant receives 404. Infrastructure failures return 503.

## Persistence and retention

Keep `claims.sqlite3` and `checkpoints.sqlite3` together on one persistent local
volume. Business state and checkpoint writes are Fernet-encrypted; keyed hashes
scope identifiers. Audit history is retained in encrypted workflow checkpoints;
there is no public audit-edit API. This is not an independently immutable/WORM
compliance archive. SQLite serializes review transactions across processes on
one host. Multi-host deployments require a server database adapter.

Claims expire after 30 days and are then unavailable for review. Each tenant is
limited to 1,000 retained claims. Run daily:

```bash
python -m claims_agent.maintenance purge-expired
```

Cleanup deletes registry rows, checkpoints, and intermediate writes. Backup
expiry is an operator policy; use the approved business retention period before
introducing real data. The in-memory graph helpers exist only for offline
examples/tests; HTTP never uses them.

## Verification and remaining work

Tests cover forged/missing credentials, permissions, tenant boundaries,
self-review, decimal limits, retries, concurrent reviews, restart recovery,
checkpoint/registry failure recovery, ciphertext canaries, expiry, and request limits.
CI audits hash-locked dependencies and scans source with Bandit.

Before real production use: configure gateway TLS/fleet quotas, managed secrets,
required branch checks, backup/restore monitoring and recovery objectives. Add a
transactional outbox and idempotent consumers before introducing external events
or payments. Notifications, analytics, and a reviewer UI are optional enhancements.

## Local Docker validation

Requires Docker Engine/Desktop with the Compose v2 plugin. From this repository:

```bash
docker compose config --quiet
docker compose up --build --wait --wait-timeout 120
python3 scripts/docker_smoke.py
docker compose down
```

The API binds only to `127.0.0.1:8012`. The default configuration deliberately
has no identity credentials: `/health` succeeds, docs return 404, and protected
requests return 503. The smoke script expects this unconfigured baseline;
it does not prove authenticated business operations or storage recovery.
Existing application tests cover those paths. The `Local Docker` workflow builds
and runs the actual image with the same Compose settings.

For authenticated local requests, provide `AUTH_ISSUER`, `AUTH_AUDIENCE`, and
`AUTH_PUBLIC_KEY` (the full RSA PEM) through your shell or a private `.env` file,
then recreate the container. Use tokens from your configured identity provider.
Never commit credentials. Model calls remain disabled in this local configuration.
The container runs as UID 65532 with a read-only root filesystem, a temporary
`/tmp`, dropped capabilities, and CPU/memory/process limits.

Also set a stable `DATA_ENCRYPTION_KEY` before exercising storage. The named
`app-data` volume is writable by UID 65532 and survives `docker compose down`.
Do not scale this SQLite service across hosts. `docker compose down --volumes`
permanently deletes local data; use it only to discard a synthetic test dataset.
The CI smoke checks volume write permissions and survival across container
recreation; it does not replace an application-consistent backup/restore test.
Run the documented retention command with `docker compose exec -T app`.

### Authenticated container scenario

Run `python3 scripts/docker_e2e.py` with Docker available. This builds the image,
uses a uniquely named Compose project and synthetic credentials, and checks
successful authorized requests plus rejection of missing credentials and missing
permissions. It recreates the container twice and checks the scenario again;
Claims resumes a paused review and preserves the final decision, while Knowledge
preserves authorized evidence and tenant/reader isolation before deletion.
Assessor repeats its deterministic assessment without invoking a model.

The harness uses the same localhost ports as the regular Compose service; stop
that service first. It deletes only its own synthetic project volumes on exit.
Your normal app volume is not used. Private signing keys stay in harness memory
and stdin; temporary public-key/encryption configuration is deleted on exit.
These synthetic credentials are exclusively for this isolated check. This test
is restart validation, not a backup/restore or disaster recovery certification.
