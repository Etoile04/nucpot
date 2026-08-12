# KG Dispatch Lifecycle — LightRAG ingest and the SQL transaction

**Status:** Active
**Applies to:** `extraction_pipeline.trigger_extraction()`, `kg_re.GraphBuilder`, `kg_lightrag_sync.fire_ingest_to_lightrag()`
**History:** NFM-2871 (fix), NFM-2920 (audit finding C6.1 — dead-code removal + this document)

## The safety property

> **LightRAG must never hold an entity that the SQL transaction did not persist.**

The knowledge graph (LightRAG) and the relational store are two separate systems with
no shared transaction. LightRAG has no rollback. Once an entity is ingested it stays
there until something explicitly deletes it. Therefore the *only* safe ordering is:

```
SQL commit succeeds  ──►  then dispatch to LightRAG
```

Anything else creates **ghost entities**: nodes and edges that exist in the graph but
have no row behind them. Ghost entities are worse than missing ones — retrieval
surfaces them as real, cited facts.

## The bug this prevents (audit finding C6.1)

`GraphBuilder` used to call a private helper, `_fire_lightrag_ingest`, from inside
`build_from_extraction()` — that is, in the middle of the SQL transaction, roughly 66
lines before `session.commit()`. The failure mode:

1. `build_from_extraction()` stages nodes/edges and dispatches them to LightRAG.
2. A later stage of the pipeline raises, or `session.commit()` itself fails.
3. SQL rolls back. The rows are gone.
4. LightRAG still holds the nodes and edges. Referential integrity between SQL and
   the KG is now broken, silently and permanently.

## The chosen pattern: carry-then-dispatch

We considered a SQLAlchemy `after_commit` event listener (the issue's stated
preference) and rejected it. Rationale:

- `after_commit` fires for **every** commit on the session, including unrelated ones
  in the same request. Scoping it back to "only this extraction job" requires
  stashing state on the session anyway — the same bookkeeping, less visible.
- The listener runs at a distance from the pipeline. A reader of
  `trigger_extraction()` cannot see that a dispatch happens, which is exactly the
  property that let C6.1 survive review the first time.
- Registering and tearing down listeners per-job interacts badly with the existing
  `db_session` test fixtures.

Instead, `BuildResult` **carries** the payload and `trigger_extraction()` **dispatches**
it explicitly after the commit returns:

```python
# kg_re.GraphBuilder.build_from_extraction() — stages only, dispatches nothing.
#   BuildResult.ingest_nodes: tuple[KGNode, ...]
#   BuildResult.ingest_edges: tuple[KGEdge, ...]

# extraction_pipeline.trigger_extraction() — the single dispatch site.
await session.commit()          # ← if this raises, everything below is skipped

if build_result and (build_result.ingest_nodes or build_result.ingest_edges):
    try:
        fire_ingest_to_lightrag(...)
    except Exception:
        logger.warning("post-commit LightRAG ingest failed (non-fatal)", exc_info=True)
```

Two properties make this safe:

- **The commit is not inside a `try`.** If `session.commit()` raises, the exception
  propagates out of `trigger_extraction()` and the dispatch block is never reached.
  The rollback path skips dispatch structurally, not by a flag anyone can forget.
- **The dispatch is inside a `try`.** LightRAG is best-effort. A graph-ingest failure
  must never fail a job whose data is already durably committed. The two directions
  are deliberately asymmetric: SQL failure blocks the graph, graph failure does not
  block SQL.

## Invariants for future changes

1. **One dispatch site.** `fire_ingest_to_lightrag()` is called from exactly one place
   in the extraction path: after `session.commit()` in `trigger_extraction()`.
   `grep -rn "fire_ingest_to_lightrag" apps/api/src` should show the import, the
   definition, and that single call.
2. **`GraphBuilder` never dispatches.** It stages rows and populates
   `BuildResult.ingest_nodes` / `.ingest_edges`. NFM-2920 deleted the dead
   `_fire_lightrag_ingest` helper so it cannot be called back into service by
   accident; a comment at the old site records why.
3. **Never wrap the commit in a `try` that continues into the dispatch.** Doing so
   re-opens C6.1 in its original form.
4. **Any new KG-mutating side effect follows the same rule** — carry it on the result
   object, fire it after the commit.

## Regression coverage

`apps/api/tests/test_extraction_pipeline.py::TestLightRAGIngestAfterCommit`

| Test | Asserts |
| --- | --- |
| `test_lightrag_not_called_when_commit_raises` | Rollback path: `session.commit()` raises → `fire_ingest_to_lightrag` is never invoked |
| ordering test | `commit` appears before `fire_ingest_to_lightrag` in the recorded call order, and the ingest is last |

The rollback test builds a KG branch that *succeeds* first, so `build_result` is
genuinely populated. Without that setup the guard would be unentered for the wrong
reason and the test would pass vacuously.
