# ADR-014 — Canonical `potentials.file_url` storage form (NFM-4309 / BUG-37)

| Field | Value |
| --- | --- |
| **Status** | Accepted (LE implementation decision, per issue brief recommendation) |
| **Date** | 2026-09-05 |
| **Author** | Lead Engineer |
| **Source issue** | [NFM-4309](/NFM/issues/NFM-4309) / GitHub [#1150](https://github.com/Etoile04/nucpot/issues/1150) (BUG-37) |
| **Supersedes** | NFM-3317 frontend-completion heuristic (`apps/web/src/lib/file-url.ts` legacy branches remain only as read tolerance) |

---

## 1. Context

`potentials.file_url` accumulated four storage shapes; two are dead links in
production and one leaks the API container's filesystem layout to every API
consumer. Prod ground truth (2026-09-05, 66 rows):

| Form | Example | Count | Status |
| --- | --- | --- | --- |
| `/uploads/<uuid>.<ext>` | `/uploads/14607d0a….tersoff` | 2 | **404** — files exist in the `prod-uploads` volume but no route serves `/uploads` on the site domain |
| `/app/uploads/<file>` | `/app/uploads/Fe_Mendelev_2007v2.eam.fs` | 1 | **404 + container-path leak**; the referenced file is gone (Hunan U. incident lineage) |
| `/storage/v1/object/public/…` | `…/potentials/huda/Ag2S_MTP.mtp` | 32 | works only after the frontend prepends the Supabase origin; raw URL useless to non-browser consumers; 8 rows are comma-separated multi-object fields |
| `https://…supabase.co/storage/v1/…` | absolute object URLs | 13 | works for direct consumers but the frontend resolver **wrapped** them as `/uploads/https://…` → dead download buttons |

Root cause: the URL field doubles as a storage pointer. Storage locations
moved (Supabase → upload volume → container path) and every move stranded the
previous cohort of rows.

## 2. Decision

1. **`file_url` stores exactly one canonical form**: the backend proxy
   download path `/api/v1/potentials/{id}/file`.
   - Anonymous (`GET`, no auth), identical for the web download button and
     direct API consumers (nginx routes `/api/*` to the API in prod;
     `DISABLE_API_REWRITE` on the web container is irrelevant to it).
   - The URL never encodes a storage location, so storage can move again
     without another data migration.
2. **Backing storage lives in `extra.file_storage`**, never in the public URL:
   - `{"kind": "uploads", "key": "<uuid>.<ext>"}` — relative to the shared
     upload dir (`prod-uploads` volume at `/app/uploads` in prod);
   - `{"kind": "supabase", "objects": ["potentials/library/x.eam.alloy", …]}` —
     object paths (multi-object rows keep the full list; `?index=` selects).
3. **The proxy resolves sources server-side**: uploads files stream via
   `FileResponse`; Supabase objects on the configured origin stream through
   the backend (httpx, 200 + bytes — plain `GET` must return the body).
   Foreign-origin absolute URLs are *not* fetched by the API container (SSRF
   guard); the proxy hands them back to the client with a `307` redirect.
   Legacy un-migrated forms are still parsed on read (tolerant reader).
4. **Write paths validate shape before persist** (`validate_persistable_file_url`):
   allowed = canonical proxy, `/uploads/<key>`, `/storage/v1/object/public/…`,
   absolute `http(s)`. Rejected: `/app/…` and any other filesystem/container
   path, `file://`, Windows/UNC paths, backslash values, bare filenames.
5. **Migration 083** rewrites all existing rows to the canonical form (idempotent;
   inventory + original values archived to the migration log and
   `/var/log/nfmd/potential_file_url_inventory_083.json`). Unrecoverable rows
   (bare filenames) are blanked with `extra.file_url_note`.
6. **Post-deploy sweep** (`apps/api/scripts/verify_potential_files.py`) enforces
   the invariant *every non-empty `file_url` anonymously downloads 200 + bytes>0*
   and blanks definitively-missing rows with a note (default dry-run, `--apply`
   writes). Transient upstream conditions (network errors, 429/5xx) and
   foreign-origin URLs (never fetched server-side) are reported as
   *unverifiable* and never blanked — the operator re-runs once the upstream
   is healthy. File existence is deliberately **not** probed by the migration
   itself — the volume is only visible where prod runs, and migration results
   must not depend on the deploy host.

## 3. Consequences

- `/uploads/` is **not** kept as a public serving route; it remains a storage
  location only. The (broken) `prod-uploads:/app/apps/web/public/uploads` mount
  on the web container becomes vestigial and can be removed in a follow-up.
- Downloads now traverse FastAPI (≤50 MB per upload cap) — acceptable for the
  current traffic; revisit with a redirect/signed-URL scheme only under BUG-34.
- Supabase-backed downloads require API-container egress to the public bucket
  (verified working 2026-09-05).
- Frontend `resolveFileUrl` keeps legacy branches for cached/un-migrated data
  but now passes absolute URLs and the canonical proxy path through unchanged.

## 4. Verification evidence

- Prod inventory + live probes: `/uploads/…` → 404 on the site domain;
  both tersoff files present in the volume; Supabase object fetch from the API
  container → 200/25,348 B.
- Tests: `tests/services/test_potential_file_resolver.py` (46 unit),
  `tests/api/v1/test_potential_file_proxy.py` (12 integration),
  `tests/test_migration_083_normalize_potential_file_urls.py` (3 runtime),
  `apps/web/src/lib/file-url.test.ts` (frontend forms).
