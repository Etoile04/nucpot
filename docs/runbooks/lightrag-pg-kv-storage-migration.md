# Runbook: LightRAG KV/doc-status → PG storage cutover (NFM-4736)

**Date:** 2026-09-12 · **Ticket:** NFM-4736 ↔ GitHub #1317 · **Risk:** medium (sidecar restart)

## Why

The sidecar ran `JsonKVStorage` + `JsonDocStatusStorage` (library defaults):
every mutation rewrites the **whole JSON file** under `/app/data`. With three
concurrent writer paths (daily 03:30Z reconciliation reingest, inline KG
extraction sync, operator replay) a writer holding an empty/stale snapshot
truncates the file. This is the structural root cause of **both** corpus
wipes (2026-09-05 and 2026-09-11, window 03:24–08:29Z, VDB
144/644/645 → 4/21/27, doc KV zeroed).

## What changed

`docker-compose.prod.yml` lightrag service env defaults:

| Var | Old default | New default |
|---|---|---|
| `LIGHTRAG_KV_STORAGE` | `JsonKVStorage` | `PGKVStorage` |
| `LIGHTRAG_DOC_STATUS_STORAGE` | `JsonDocStatusStorage` | `PGDocStatusStorage` |

`LIGHTRAG_VECTOR_STORAGE` was already `PGVectorStorage`.
`LIGHTRAG_GRAPH_STORAGE` stays `NetworkXStorage`: `PGGraphStorage` requires
the AGE extension, which the `pgvector/pgvector:pg16` image does not ship.
The graph is fully derivable by re-ingest.

No schema migration: stock lightrag-hku 1.5.4 `PGKVStorage` /
`PGDocStatusStorage` key on `(workspace, id)` and `CREATE TABLE IF NOT
EXISTS`; the `lightrag_doc_status` / `lightrag_doc_full` /
`lightrag_doc_chunks` tables already exist in `nfm_db` with compatible
columns, `workspace='_app_data'`.

## Cutover steps

1. Merge the PR; the auto-deploy rebuilds + recreates the sidecar.
2. Verify the sidecar is healthy and actually using PG storage:

   ```bash
   DOCKER_HOST=unix:///var/run/nfm-g2/docker-ro.sock docker exec \
     nucpot-prod-lightrag python -c \
     "import urllib.request,json;print(json.dumps(json.loads(urllib.request.urlopen('http://127.0.0.1:9621/health').read()),indent=1))" | grep -i storage
   ```

   (run from a full-gate context; desktop exec is G5-refused)
3. Clean the stale test rows so they don't surface as failed docs
   (6 rows, one bulk microsecond write, ids like `doc-1a0f45d9-test` —
   repro-script residue, not runtime data):

   ```bash
   sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-sql.sh - <<'SQL'
   DELETE FROM lightrag_doc_status WHERE workspace='_app_data';
   SQL
   ```

4. Rebuild the literature corpus (see NFM-4736 rebuild procedure:
   dispatch `process_literature_task` per completed literature; the
   canonical path is what `rag_audit_index_coverage` uses).
5. Acceptance (from the ticket): baseline query `UO2 热导率` returns a
   non-empty cited answer; doc KV ≥14 docs; consecutive daily audits with
   zero missing.

## Rollback

Revert the compose default (or set `PROD_LIGHTRAG_KV_STORAGE=JsonKVStorage`
+ `PROD_LIGHTRAG_DOC_STATUS_STORAGE=JsonDocStatusStorage` in
`docker/.env.prod`) and redeploy. The pre-wipe JSON files on the
`nucpot-prod-lightrag-data` volume are stale (last written 2026-09-11) —
after rollback, re-run the corpus rebuild to repopulate them.
