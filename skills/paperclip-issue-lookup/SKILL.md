---
name: paperclip-issue-lookup
description: Safe Paperclip issue lookups — the only sanctioned way to fetch or list issues from agent code. Wraps `paperclip_issue_lookup.py` and prevents the 401/error-object misread that falsely concludes an issue was deleted.
metadata:
  type: ephemeral
  companySkill: true
  owner: lead-engineer
  references:
    - NFM-2036
    - NFM-2037
    - NFM-2038
    - NFM-1909
---

# Paperclip issue lookup — use the helper

## What this is

The sanctioned way for any agent in this company to fetch a single
issue or list issues from the Paperclip API. Bypassing this helper
re-opens the trap that caused the CTO to assert [NFM-1909](/NFM/issues/NFM-1909)
was deleted across three heartbeats on 2026-07-29.

See the architectural spec on [NFM-2036](/NFM/issues/NFM-2036#document-arch-spec)
(Layer 1) for the full rationale.

## The script

```text
scripts/paperclip_issue_lookup.py
```

**Public surface — exactly two functions:**

```python
from paperclip_issue_lookup import lookup_issue, lookup_issues

result = lookup_issue("NFM-1909")
result = lookup_issues(q="weekly standup", status=["in_progress", "todo"])
```

## Discriminated result vocabulary

`LookupResult` is a tagged union. The two caller-environment bugs
arrive as **exceptions**, never return values, so they can never be
mistaken for an empty list.

| Shape | How it arrives | Meaning |
|-------|---------------|---------|
| `Ok(issues, pages_consumed, truncated=False)` | returned | Genuine result. `issues` may legitimately be empty. Inspect `truncated` before treating it as the complete set. |
| `NotFound(identifier, http_status=404)` | returned | The identifier was searched for and genuinely has no match. |
| `ApiError(http_status, body, kind)` | returned | The API returned something we could not interpret as issues. `kind` is `"server"`, `"rate_limited"`, or `"unknown"`. |
| `AuthError(http_status, message, hint, preflight=False)` | **raised** | Credentials missing or rejected. Failing loud is the point. |
| `WrongPath(called_path, hint)` (alias `WrongPathError`) | **raised** | URL is not company-scoped. The hint names the correct path. |

**Never conclude "issue was deleted" from anything other than `NotFound`.**

## Hard requirements

1. **Pre-flight auth.** The helper inspects `PAPERCLIP_API_KEY` *before*
   opening any HTTP connection. A missing key raises `AuthError` locally.
2. **Path validator.** Any URL whose path does **not** contain
   `/api/companies/{companyId}/` raises `WrongPathError` before any
   HTTP call.
3. **Auto-pagination.** `lookup_issues` walks `offset` in 1000-row
   chunks (cap `max_pages`, default 10). Exposes `pages_consumed`.
4. **`truncated: bool` on `Ok`.** True when the page cap was hit without
   a short read. Caller must inspect.
5. **No new dependencies.** Python 3.11+ stdlib + `requests` (already
   in the worktree).

## Acceptance gate

```bash
python3 scripts/verify_paperclip_issue_lookup.py
```

Exits non-zero unless all four cases pass:

| # | Setup | Expected |
|---|-------|----------|
| 1 | `PAPERCLIP_API_KEY` unset | `AuthError` raised, **0 HTTP calls** (asserted by stubbing `requests`) |
| 2 | `BASE_URL` patched to bare `/api/issues` | `WrongPathError` raised, **0 HTTP calls** |
| 3 | `lookup_issue("NFM-DOES-NOT-EXIST-9999")` | `NotFound` returned, distinct from errors |
| 4 | `lookup_issue("NFM-1909")` | `Ok(issues=[<NFM-1909>], pages_consumed=1)` |

## Usage example

```python
from paperclip_issue_lookup import lookup_issue, Ok, NotFound
from paperclip_issue_lookup import AuthError, WrongPathError, ApiError

try:
    result = lookup_issue("NFM-1909")
except AuthError as err:
    # Re-raise or fail loud — never retry silently.
    raise
except WrongPathError as err:
    # Config bug. Fix the URL.
    raise

match result:
    case Ok(issues=[issue]):
        if issue["identifier"] == "NFM-1909":
            print("found it")
    case NotFound(identifier=ident):
        print(f"{ident} genuinely does not exist")
    case ApiError() as err:
        # Server / rate-limited / unknown shape.
        raise SystemExit(err)
```

## What NOT to do

- **Do not** call `GET /api/issues/...` directly. The bare path returns
  an error object that a naive `len()` reads as "zero issues match".
- **Do not** infer "issue is deleted" from any non-list response. The
  helper returns a discriminated result; trust the `kind` field.
- **Do not** wrap the helper in a "convenience" layer that drops the
  discriminated result. The whole point is that callers must branch on
  `Ok` / `NotFound` / `ApiError` explicitly.

## Reference

- Architectural spec: [arch-spec on NFM-2036](/NFM/issues/NFM-2036#document-arch-spec)
- Implementation issue: [NFM-2037](/NFM/issues/NFM-2037)
- Origin / post-mortem: [NFM-1909](/NFM/issues/NFM-1909)
- CEO corrective comment on NFM-1909: `54a7817d`
