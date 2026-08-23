"""Snapshot-diff harness for V1 (hardcoded STANDARD_PROPERTIES) vs
V2 (ontology-only) extraction prompt paths.

Lives in the test tree on purpose: the harness must NOT modify
production code. It reconstructs the pre-NFM-3258 V1 prompt builders
locally so the V1 path remains runnable for baseline comparison
even after the hardcoded import was removed from
``nfm_db.services.extraction_prompt``.

See ``docs/verification/NFM-3531-v1-v2-baseline.md`` for the baseline
diff report and ``apps/api/tests/extraction/run_snapshot_diff.py``
for the standalone CLI entrypoint.
"""
