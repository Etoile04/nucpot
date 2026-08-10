"""Celery tasks for the gap-driven collection pipeline (NFM-2781 CR3).

The :func:`process_gap_literature_task` worker was previously defined
at the bottom of :mod:`nfm_db.services.gap_dispatch_service` and
imported Celery at module load — violating that module's
``broker-free`` docstring.  Splitting the task into this dedicated
``tasks`` package keeps the dispatch service broker-free and gives the
worker a single home that's easy to test and patch.
"""
