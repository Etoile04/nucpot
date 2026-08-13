"""Concrete ExtractionStep implementations (NFM-2677 B2-B6).

Each module in this sub-package defines one step of the strangler-fig
pipeline. Steps are pure value transformers — they take an
``ExtractionChunk`` in and emit a new ``ExtractionChunk`` out, never
mutating the input. The orchestrator (B7) composes them in order.
"""
