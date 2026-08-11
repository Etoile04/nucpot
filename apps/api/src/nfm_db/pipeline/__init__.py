"""Pipeline subpackage — NFM-2676 strangler-fig decomposition.

Defines the canonical :class:`~nfm_db.pipeline.extraction_step.ExtractionStep`
Protocol and shared :class:`~nfm_db.pipeline.extraction_step.StepContext` /
:class:`~nfm_db.pipeline.extraction_step.StepResult` dataclasses that
every concrete pipeline step must conform to.

The V2 extraction pipeline (:class:`nfm_db.services.extraction_orchestrator.ExtractionOrchestrator`)
is gated by :attr:`nfm_db.config.Settings.extraction_v2_enabled` and runs
the five canonical step types declared in
:data:`nfm_db.models.extraction_step.EXTRACTION_STEP_TYPES` (``chunk``,
``extract``, ``map``, ``quality_gate``, ``gap_scan``).
"""
