# NFM-4926 — Reingest-burst pacing (Option A): implementation + deploy notes

Branch notes for Release Engineer → CTO/CPO hand-off. Parent: NFM-4924
(Option A). Decision chain: NFM-4923 (CEO) → NFM-4924 (CTO) → NFM-4926
(this implementation). Full RCA: NFM-4922.

## What changed

1. **Wave pacing** — `apps/api/src/nfm_db/services/rag_audit.py`
   (`run_rag_audit_index_coverage` drift loop): drift docs dispatch in
   waves with a bounded drain-check between consecutive waves. Per-doc
   dispatch error handling (try/except + `_record(action="error")` +
   continue) is preserved verbatim in semantics. No Celery `rate_limit`
   on `process_literature_task` — pacing is scoped to the burst path
   only (CTO direction 1).
2. **Settings** — `apps/api/src/nfm_db/config.py`:
   `rag_audit_wave_size` (default 4) and `rag_audit_wave_drain_timeout_s`
   (default 240.0), env-tunable via the repo's pydantic-settings pattern
   (`NFM_` prefix — see "Naming deviation" below). The Celery wrapper
   (`celery_app.py`) wires them through and logs the effective values at
   task start (`rag_audit: wave pacing wave_size=… drain_timeout_s=…`).
3. **`MAX_ASYNC_LLM` 4 → 2** — in **both** compose files; see the
   runtime-effectivity section below for why two files were needed.

## Semantics (pinned by unit tests)

- Wave chunking: `N` docs dispatch as `ceil(N / wave_size)` waves; the
  final partial wave still dispatches.
- Drain-check runs **only between** waves — the final wave has no
  successor, so `N <= wave_size` behaves exactly like the pre-NFM-4926
  fire-and-forget fan-out (zero added waiting).
- Drain is readiness-only (a FAILED task result still counts as
  drained); per-doc failure accounting stays with the ingest pipeline,
  unchanged.
- Drain timeout is bounded and non-fatal: on timeout the next wave
  dispatches anyway (warning logged with ready counts) — pacing never
  hangs the audit.
- Dispatch order is deterministic (drift set sorted before slicing).
- A dispatch that raises inside a wave records its own `error` audit row
  and does not prevent the rest of the wave or later waves.

## Naming deviation from the CTO sketch (flag for architecture review)

CTO direction 3 sketched the env names as `RAG_AUDIT_WAVE_SIZE` /
`RAG_AUDIT_WAVE_DRAIN_TIMEOUT_S`. The repo's existing settings pattern
(pydantic `Settings` + `env_prefix = "NFM_"`) yields
`NFM_RAG_AUDIT_WAVE_SIZE` / `NFM_RAG_AUDIT_WAVE_DRAIN_TIMEOUT_S`. The
direction said "following the repo's existing settings pattern", so the
prefix was kept for consistency with every other knob
(`NFM_LIGHTRAG_*`, `NFM_PRIORITY_*`, …). Same mechanics, prefixed names.

## `MAX_ASYNC_LLM=2` — runtime effectivity (deploy-runbook step)

**Finding that forced a second file:** the prod lightrag container is
launched from **`docker-compose.prod.yml`** (label
`com.docker.compose.project.config_files=/var/lib/nfmdeploy/Projects/nucpot/docker-compose.prod.yml`,
verified 2026-09-17), NOT from `docker-compose.lightrag.yml` that the
charter cited. The prod service never set `MAX_ASYNC_LLM` — it ran on
the LightRAG library internal default (4), and a flip limited to
`docker-compose.lightrag.yml` would have been a **prod no-op**.

Both files are changed:

- `docker-compose.lightrag.yml`: `${LIGHTRAG_MAX_ASYNC_LLM:-4}` → `:-2`
  (the charter-cited surface; covers stacks launched from that file).
- `docker-compose.prod.yml` (lightrag service, LLM config block):
  adds `MAX_ASYNC_LLM: ${PROD_LIGHTRAG_MAX_ASYNC_LLM:-2}`.

**Host env pin check (done 2026-09-17):** neither `~/Projects/nucpot/.env.prod`
nor `~/Projects/nucpot/docker/.env.prod` pins
`LIGHTRAG_MAX_ASYNC_LLM` or `PROD_LIGHTRAG_MAX_ASYNC_LLM` — the compose
defaults will be runtime-effective as-is. (They DO pin
`PROD_LIGHTRAG_LLM_*`, which is how ollama/qwen3.5:4b-nvfp4 reach the
container; those are untouched.)

**Post-deploy verification (execute verbatim on the prod host):**

```bash
docker inspect nucpot-prod-lightrag --format \
  '{{range .Config.Env}}{{println .}}{{end}}' | grep MAX_ASYNC_LLM
# expected output: MAX_ASYNC_LLM=2
```

Optionally also confirm the worker picked up the pacing knobs (any
03:30Z audit log line):

```bash
docker logs nucpot-prod-worker 2>&1 | grep 'wave pacing' | tail -1
# expected: rag_audit: wave pacing wave_size=4 drain_timeout_s=240
```

If a future host env ever pins `PROD_LIGHTRAG_MAX_ASYNC_LLM`, the pinned
value wins over the compose default — update the pin, not the compose
file (mirror any change across `~/Projects/nucpot/.env.prod` AND
`~/Projects/nucpot/docker/.env.prod`; see NFM-4804 for the two-file
parity trap).

## Hard-constraint verification (pre-hand-off)

`scripts/host-prod-gate/entries/ollama-runner-term.sh` and
`scripts/host-prod-gate/entries/start-lightrag-watchdog.sh` are **not
part of this branch's diff** (byte-identical to 62909dc3c); no probe
scheduling or probe-model files touched; no host-runner env
(`OLLAMA_NUM_PARALLEL` / `OLLAMA_CONTEXT_LENGTH` / KV quant) introduced;
lightrag client deadlines (8s query / 300s ingest) untouched.

## CI

- `uv run ruff check src tests` — clean.
- `uv run mypy src` — clean (358 files).
- `python apps/api/scripts/check_silent_excepts.py` — clean.
- `uv run pytest tests/services/test_rag_audit.py
  tests/services/test_rag_audit_buckets.py` — 69 passed (8 new
  wave-pacing tests + 2 updated wrapper-fake assertions).
- Full `uv run pytest` (ci.yml api job) — see PR checks.
