# Security and deployment boundary

This is a hardened portfolio service, not a certification of production readiness.
Only `/health` is public. Interactive API docs are disabled. Protected requests
fail closed when token verification or encrypted storage is not configured.

## Identity

Configure `AUTH_PUBLIC_KEY` with an RSA PEM public key (2048 bits or stronger),
`AUTH_ISSUER`, and `AUTH_AUDIENCE`. RS256 is the only accepted algorithm; the
application never follows token-supplied key URLs. Tokens require `sub`,
`tenant_id`, `iat`, `exp`, `iss`, and `aud`; `permissions` is a list of exact
permission strings. Obtain tokens from your identity provider, never mint them
from client-supplied user or tenant names. Use short-lived access tokens.
Rotate public keys through a coordinated rollout; this adapter accepts one key
at a time and does not implement live JWKS rotation or token revocation.

## Runtime and data

Terminate HTTPS at a trusted gateway. Use managed runtime identity and secret
injection. Never commit private keys, bearer tokens, or encryption keys. Keep
request bodies and authorization headers out of application, gateway, and APM
logs. Disable third-party workflow tracing for sensitive data unless approved.
The application does not send sensitive data to telemetry deliberately.

Request bodies are bounded before JSON parsing, including chunked requests;
compressed requests are rejected. Each process permits 16 concurrent HTTP
requests, 60 requests per tenant per minute, and a 10-second body-read deadline.
Put fleet-wide rate/cost quotas and a connection limit at the gateway; the local
limiter is intentionally not a distributed quota. Set gateway header/body limits
and idle timeouts. Optional model calls use a 10-second SDK timeout, one attempt,
and bounded output. Cloud SDK timeouts are not a hard process kill deadline.

Encrypted SQLite adapters require a private, persistent local directory and
`DATA_ENCRYPTION_KEY` (a Fernet key). Ciphertext includes authentication so
modified records fail closed. Restrict the volume and backups to the service
identity. SQLite is a single-host adapter, not a multi-host database. Do not put
these files on NFS or give each replica its own isolated volume. Deploy one
replica with a persistent volume, or replace the adapter with a server database
before scaling across hosts. Take consistent backups while requests are drained,
and test restore with the same key in an isolated environment.

Fernet encryption protects record bodies; database schema, counts, expiry times,
and orchestration metadata are not hidden. Identifiers are keyed hashes. Key
rotation requires an explicit data migration because the key also scopes those
hashes; changing the environment key alone does not migrate old data. Keep the
key separate from encrypted backups and retain it for their approved lifetime.

## Release and operations

CI runs integration/security tests on Python 3.11 and 3.12, installs hash-locked
dependencies, audits the lockfile for known vulnerabilities, and runs Bandit.
Update `requirements.lock` with `uv pip compile pyproject.toml --extra test`
plus the repository's optional model extra, `--python-version 3.11`, and
`--generate-hashes`. Review lockfile updates and security findings before merge.
Configure branch rules to require `test (3.11)`, `test (3.12)`,
`dependency-audit`, and `static-security`; workflow files cannot enforce their
own required-check status.

Before an external deployment, set explicit storage, model-cost, availability,
backup, and recovery objectives; provision monitoring for errors, saturation,
model fallback, and disk utilization. `/health` is liveness only, not a database
or provider readiness guarantee. Deploy a canary, run synthetic smoke requests,
then increase traffic. Roll back the application image on regression; do not
restore an old database over new decisions without a reconciliation plan.

## Retention and deletion

Run the repository's retention command from an operator job at least daily.
Expired records are excluded from normal access immediately, then physically
removed by cleanup. Keep backups encrypted; their deletion follows a separately
specified backup expiry policy. Deletion does not erase copies previously sent
to an external model or retained by a client. Obtain approval for the model data
flow before enabling it. No compliance certification is implied.

## Reporting

Do not publish real secrets or customer records in a GitHub issue. Provide a
synthetic reproduction and affected commit through a private reporting channel.
