"""Snapshot-diff parity harness (NFM-3581).

Compares V1-hardcoded prompt path against V2-ontology-only prompt path on a
golden set of representative inputs. Emits a markdown report classifying each
delta as pass / warn / fail with cosmetic / non-cosmetic / blocking annotations.

Public entry point:
    harness.run_diff() -> DiffReport
"""
