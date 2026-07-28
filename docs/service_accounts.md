# Service Accounts

> NFM-1973 / NFM-1972 AC-1 — machine-to-machine identities for OntoFuel
> and future automation.

Service accounts authenticate via the standard `/auth/login` endpoint
but are gated to **`/api/v1/extraction/ingest`** only. Every other API
route — including all `/admin/*`, `/auth/roles*`, and
`/extraction/trigger` — returns `403 Forbidden` for service-account
identities.

This document covers:

1. [How service accounts differ from human users](#how-service-accounts-differ)
2. [Provisioning a new service account](#provisioning)
3. [Authenticating and calling the API](#authenticating)
4. [JWT TTL and rotation](#jwt-ttl-and-rotation)
5. [RBAC enforcement model](#rbac-enforcement-model)
6. [Troubleshooting](#troubleshooting)

---

## How service accounts differ

| Aspect             | Human user                                  | Service account                                     |
| ------------------ | ------------------------------------------- | --------------------------------------------------- |
| Table row flag     | `is_service_account = false`                | `is_service_account = true`                         |
| `blog_role`        | `admin` / `editor` / `reviewer` / `NULL`    | always `NULL`                                       |
| Authorization unit | `BlogRole` + `Permission` set               | single `ServiceAccountScope` (`extraction:ingest`)  |
| Token claims       | `{"sub": <user_id>}`                        | `{"sub": <user_id>, "is_service_account": true, "scope": "extraction:ingest"}` |
| Token TTL          | `NFM_ACCESS_TOKEN_EXPIRE_MINUTES` (default 30) | `NUCPOT_SERVICE_JWT_TTL_MINUTES` (default 30)       |
| Allowed endpoints  | anything the `BlogRole` permits             | **`/api/v1/extraction/ingest` only**                |
| Password expiry    | rotates with operator policy                | not subject to expiration rules (NFM-1973 AC-3)     |
| Email              | operator-supplied, real inbox               | synthetic `<username>@service.local` (RFC 6762)      |

The `users` table holds both populations; the `is_service_account`
boolean partitions them. The two authorization machinerys
(`require_blog_role` / `require_permission` for humans,
`require_service_scope` for services) are **mutually exclusive** — a
service account can never piggyback on human privileges, and a human
can never piggyback on service privileges, even if the JWT is forged
or the row is demoted after the fact.

---

## Provisioning

Service accounts are created via the `nucpot` console script, not via
the HTTP `/auth/register` endpoint (humans can self-register; machines
cannot, by policy).

```bash
# From the API package directory (apps/api):
uv run nucpot create-service-account \
    --username ontofuel-svc \
    --role service
```

The command:

1. Generates a 256-bit cryptographically random password via
   `secrets.token_urlsafe(32)` (43 URL-safe characters).
2. Inserts a `users` row with `is_service_account=true`,
   `is_active=true`, `blog_role=NULL`, and the bcrypt-hashed password.
3. Prints the plaintext password **once** to stdout, framed by a banner
   that tells the operator to save it to a password manager.
4. Refuses to overwrite an existing username (so JWTs issued against the
   old hash stay valid until their TTL expires — no silent
   invalidation surprises).

Output looks like:

```text
========================================================================
Service account created: ontofuel-svc
  user_id        = 5d2d8e3a-...
  is_service_account = True
  is_active      = True
  email (synthetic)  = ontofuel-svc@service.local
  created_at     = 2026-07-28T22:14:03.512345+00:00
========================================================================

ONE-TIME PASSWORD (copy now — cannot be recovered):

    k3FQ...truncated...wA8

========================================================================
Save this password in your password manager before closing the
terminal.  The plaintext is not stored anywhere in the database
or application logs.
========================================================================
```

The `nucpot` console script requires `NFM_DATABASE_URL` to point at the
target database (Pydantic settings prefix; see `apps/api/src/nfm_db/config.py`).
The command bypasses the running FastAPI server — it talks to the
database directly through SQLAlchemy.

### Idempotency

Re-running the same command returns a non-zero exit code and a
click-style error; the original row is untouched. To rotate a
service-account password, **delete the row** (`DELETE FROM users WHERE
username='ontofuel-svc'`) and re-run `create-service-account`. Note
that any in-flight JWTs remain valid until their TTL expires — the
deletion only blocks future logins.

### Removing a service account

```sql
UPDATE users SET is_active = false WHERE username = 'ontofuel-svc';
```

`is_active=false` makes the row unable to log in (`get_current_active_user`
raises `403 Inactive user`). The row is retained for audit; drop it
once any compliance retention period has elapsed.

---

## Authenticating

A service account authenticates with the standard OAuth2 form-encoded
`/api/v1/auth/login` endpoint:

```bash
curl -sS -X POST "$NUCPOT_API/api/v1/auth/login" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     --data-urlencode "username=ontofuel-svc" \
     --data-urlencode "password=$THE_ONE_TIME_PASSWORD"
```

The response is `{"access_token": "<jwt>", "token_type": "bearer"}`.
The JWT carries three claims:

- `sub` — the user's UUID
- `is_service_account: true`
- `scope: "extraction:ingest"`

Subsequent requests send the bearer token in the `Authorization` header:

```bash
curl -sS -X POST "$NUCPOT_API/api/v1/extraction/ingest" \
     -H "Authorization: Bearer $JWT" \
     -H "Content-Type: application/json" \
     -d '{
       "source_reference": "10.1016/j.jnucmat.2024.01.001",
       "source_type": "doi",
       "element_systems": ["U", "O"],
       "properties": [{"property_name": "lattice_constant", "value": 5.47, "unit": "angstrom"}]
     }'
```

### Cookie vs. Authorization header

`/auth/login` also sets an `HttpOnly` `access_token` cookie for browser
clients. Machine clients (OntoFuel, scripts) should use the
`Authorization: Bearer` header — never the cookie — because cookies
are subject to CSRF, SameSite, and secure-context rules that do not
apply to bearer tokens.

### What happens when a service account tries to call a forbidden endpoint

`403 Forbidden` with a JSON detail:

| Endpoint tried             | Detail returned                                                           |
| -------------------------- | ------------------------------------------------------------------------- |
| `/api/v1/auth/roles`       | `"Service accounts cannot access this endpoint"`                          |
| `/api/v1/extraction/trigger` | `"Service accounts cannot access this endpoint"`                       |
| Any `require_blog_role`    | `"Service accounts cannot access this endpoint"`                          |
| Any `require_permission`   | `"Service accounts cannot access this endpoint"`                          |
| `/api/v1/extraction/ingest` with no token | `401 Unauthorized` (from upstream auth)                  |
| `/api/v1/extraction/ingest` with a human (admin) token | `"Service account credentials required"` |

The OntoFuel client should treat all `403` responses on
`/extraction/ingest` as **configuration drift** (a human token leaked
into the M2M channel, or the service account was demoted) and alert.

---

## JWT TTL and rotation

Service-account JWT TTL is configured via:

```bash
NUCPOT_SERVICE_JWT_TTL_MINUTES=15  # tighten for high-risk callers
```

Default is **30 minutes**. The setting is read at JWT issuance time
(not at verification), so changing the env var only affects tokens
issued after the API process restarts. To rotate *credentials* (not
TTL), see [Removing a service account](#removing-a-service-account)
above — delete and recreate.

Independent of JWT TTL, operators should:

1. Provision service accounts with the **minimum scope they need**
   (today that's only `extraction:ingest`).
2. Store the one-time password in a password manager / secret store
   (1Password, Vault, AWS Secrets Manager, etc.).
3. Rotate the credential at least every 90 days — delete and recreate.
4. Alert on any service-account JWT issuance outside business hours.

---

## RBAC enforcement model

The `require_service_scope(scope)` dependency factory enforces three
checks (in order) on every protected request:

1. **JWT carries `is_service_account: true`** — humans cannot mint a
   service token by mistake.
2. **JWT's `scope` claim equals the requested scope** — a token issued
   for one endpoint cannot access another.
3. **DB row has `is_service_account=true`** — belt-and-suspenders
   against a forged token or a post-issuance demotion.

If any check fails, the response is `403 Forbidden` and **the handler
is never invoked** — there is no chance of accidental privilege
escalation via service-account identity.

The dual JWT-claim + DB-flag model means an attacker would need to
simultaneously forge the JWT secret *and* corrupt the user row to
bypass the gate — which is a strictly stronger posture than a single
check.

---

## Troubleshooting

| Symptom                                                                | Likely cause                                                              | Fix                                                                                  |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `POST /api/v1/extraction/ingest` returns `403`                         | Token is human-shaped (no `is_service_account` claim)                     | Re-auth via `/auth/login`; verify the JWT contains `is_service_account: true`        |
| Login succeeds but every subsequent call returns `401`                 | JWT TTL too short for the client; clock skew                             | Increase `NUCPOT_SERVICE_JWT_TTL_MINUTES`; ensure NTP on both sides                  |
| `User 'X' already exists` when re-running the CLI                     | Operator wants to rotate the password                                    | `DELETE FROM users WHERE username='X'` then re-run `create-service-account`          |
| `503` on `/extraction/ingest` after deploy                             | API process not restarted after `NUCPOT_SERVICE_JWT_TTL_MINUTES` changed | Restart the api/worker containers; tokens issued before the change still carry old TTL |
| `Service account credentials required` despite a fresh login           | Cookie was sent but the JWT decodes without service claims               | Use `Authorization: Bearer` header, not the cookie, in M2M clients                  |

---

## Reference: file map

| File                                                                           | Purpose                                                                                       |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `apps/api/migrations/versions/029_add_user_service_account_flag.py`            | Alembic migration adding the `is_service_account` column                                      |
| `apps/api/src/nfm_db/models/user.py`                                           | `ServiceAccountScope` enum, `is_service_account` column, `User.permissions` override          |
| `apps/api/src/nfm_db/config.py`                                                | `service_jwt_ttl_minutes` reading `NUCPOT_SERVICE_JWT_TTL_MINUTES`                            |
| `apps/api/src/nfm_db/services/auth_service.py`                                 | `create_service_account_token`, `is_service_token_payload`, `token_scope` helpers             |
| `apps/api/src/nfm_db/api/v1/auth.py`                                           | `require_service_scope(scope)` dependency factory; `require_blog_role`/`require_permission` reject service accounts |
| `apps/api/src/nfm_db/api/v1/auth_endpoints.py`                                 | `/auth/login` mints service tokens when `user.is_service_account`                             |
| `apps/api/src/nfm_db/api/v1/extraction.py`                                     | `POST /api/v1/extraction/ingest` — the only endpoint that admits service tokens               |
| `apps/api/src/nfm_db/cli/__init__.py`, `cli/main.py`, `cli/service_accounts.py` | `nucpot create-service-account` Click command                                                |
| `apps/api/pyproject.toml`                                                      | `[project.scripts] nucpot = "nfm_db.cli.main:cli"`; `click>=8.1` dependency                   |
| `apps/api/tests/test_service_account_rbac.py`                                  | HTTP-layer RBAC tests (login shape, ingest happy path, `/admin/*` 403, E2E login → ingest)    |
| `apps/api/tests/test_cli_service_accounts.py`                                  | CLI unit tests (password entropy, row shape, bcrypt, duplicate handling, banner output)      |