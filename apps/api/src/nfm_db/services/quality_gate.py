"""Quality gate service for reference value staging pipeline.

Per NFM-54 design Section 3:
- Three-path confidence router (high/medium/low)
- Dedup hash via SHA256 on 5-field key
- Range validation using property-mapping.json

The property-mapping.json is loaded from the nfm-ref-gapfill package
when available, or from a configurable fallback path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)

logger = logging.getLogger(__name__)

# Default path to property-mapping.json (bundled with nfm-ref-gapfill)
_DEFAULT_PROPERTY_MAPPING_PATH = Path(
    __file__
).resolve().parent.parent.parent.parent.parent / "property-mapping.json"


class GateDecision(StrEnum):
    """Outcome of the quality gate evaluation."""

    AUTO_APPROVED = "auto_approved"
    PENDING_REVIEW = "pending_review"
    PENDING_FLAGGED = "pending_flagged"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ValidationResult:
    """Result of range validation for a single property/value pair."""

    is_valid: bool
    property_name: str
    value: float
    min_bound: float | None = None
    max_bound: float | None = None


@dataclass(frozen=True)
class GateResult:
    """Full result of quality gate processing for one reference value."""

    decision: GateDecision
    dedup_hash: str
    confidence: Confidence
    range_validated: bool
    range_detail: ValidationResult | None = None
    error: str | None = None

    @property
    def should_stage(self) -> bool:
        """Whether the record should be inserted into staging."""
        return self.decision in (
            GateDecision.AUTO_APPROVED,
            GateDecision.PENDING_REVIEW,
            GateDecision.PENDING_FLAGGED,
        )


@dataclass(frozen=True)
class BulkGateResult:
    """Result of processing multiple reference values through the gate."""

    accepted: list[GateResult]
    rejected: list[GateResult]
    duplicates: list[GateResult]


class PropertyMappingLoader:
    """Loads and caches property range definitions from JSON.

    Expected JSON structure (from nfm-ref-gapfill property-mapping.json):
    {
        "property_name": {
            "min": <float>,
            "max": <float>
        },
        ...
    }
    """

    def __init__(self, mapping_path: Path | str | None = None) -> None:
        self._path = Path(mapping_path) if mapping_path else _DEFAULT_PROPERTY_MAPPING_PATH
        self._ranges: dict[str, dict[str, float]] | None = None

    def load(self) -> dict[str, dict[str, float]]:
        """Load property ranges from JSON file.

        Returns a dict mapping property_name -> {"min": ..., "max": ...}.
        Returns empty dict if file not found (fail-open for unknowns).
        """
        if self._ranges is not None:
            return self._ranges

        try:
            text = self._path.read_text(encoding="utf-8")
            self._ranges = json.loads(text)
            logger.info("Loaded property mapping from %s", self._path)
        except FileNotFoundError:
            logger.warning(
                "Property mapping not found at %s — range checks will fail-open",
                self._path,
            )
            self._ranges = {}
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in property mapping: %s", exc)
            self._ranges = {}

        return self._ranges

    def reload(self) -> dict[str, dict[str, float]]:
        """Force-reload the property mapping from disk."""
        self._ranges = None
        return self.load()


def compute_dedup_hash(
    element_system: str,
    phase: str | None,
    property_name: str,
    method: str | None,
    source: str,
) -> str:
    """Compute SHA256 dedup hash on 5-field key.

    Per NFM-54 design: SHA256("{element_system}|{phase}|{property_name}|{method}|{source}").
    Same 5-field key as write_ref_value.py in nfm-ref-gapfill.
    """
    key = "|".join([
        str(element_system or ""),
        str(phase or ""),
        str(property_name or ""),
        str(method or ""),
        str(source or ""),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def validate_range(
    property_name: str,
    value: float,
    ranges: dict[str, dict[str, float]],
) -> ValidationResult:
    """Check whether a value falls within the expected range for its property.

    Fail-open for unknown properties: returns is_valid=True when no range
    is defined in the mapping.
    """
    property_range = ranges.get(property_name)

    if property_range is None:
        return ValidationResult(
            is_valid=True,
            property_name=property_name,
            value=value,
        )

    min_bound = property_range.get("min")
    max_bound = property_range.get("max")

    is_valid = True
    if min_bound is not None and value < min_bound:
        is_valid = False
    if max_bound is not None and value > max_bound:
        is_valid = False

    return ValidationResult(
        is_valid=is_valid,
        property_name=property_name,
        value=value,
        min_bound=min_bound,
        max_bound=max_bound,
    )


class QualityGateService:
    """Three-path confidence router with dedup and range validation.

    Usage:
        gate = QualityGateService(session, mapping_loader)
        result = await gate.process(ref_data)
        if result.should_stage:
            # insert into staging
    """

    def __init__(
        self,
        session: AsyncSession,
        mapping_loader: PropertyMappingLoader | None = None,
    ) -> None:
        self._session = session
        self._mapping_loader = mapping_loader or PropertyMappingLoader()

    async def check_duplicate(self, dedup_hash: str) -> bool:
        """Return True if a record with this dedup_hash already exists in staging."""
        stmt = select(RefGapFillStaging.id).where(
            RefGapFillStaging.dedup_hash == dedup_hash,
        ).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    def _route_confidence(
        self,
        confidence: Confidence,
        range_validated: bool,
    ) -> tuple[GateDecision, StagingStatus]:
        """Three-path confidence router per NFM-54 design Section 3.1.

        - high  + range_valid → auto_approve (status=approved)
        - medium + range_valid → pending_review (status=pending)
        - low   → pending_flagged (status=pending)
        - any   + range_invalid → rejected
        """
        if not range_validated:
            return GateDecision.REJECTED, StagingStatus.REJECTED

        if confidence == Confidence.HIGH:
            return GateDecision.AUTO_APPROVED, StagingStatus.APPROVED
        elif confidence == Confidence.MEDIUM:
            return GateDecision.PENDING_REVIEW, StagingStatus.PENDING
        elif confidence == Confidence.LOW:
            return GateDecision.PENDING_FLAGGED, StagingStatus.PENDING
        else:
            return GateDecision.REJECTED, StagingStatus.REJECTED

    async def process(self, ref_data: dict[str, Any]) -> GateResult:
        """Process a single reference value through the quality gate.

        Steps:
        1. Compute dedup hash
        2. Check for duplicates
        3. Validate range
        4. Route by confidence
        """
        element_system = str(ref_data.get("element_system", ""))
        phase = ref_data.get("phase")
        property_name = str(ref_data.get("property", ref_data.get("property_name", "")))
        method = ref_data.get("method")
        source = str(ref_data.get("source", ""))

        # Step 1: Dedup hash
        dedup_hash = compute_dedup_hash(
            element_system=element_system,
            phase=phase,
            property_name=property_name,
            method=method,
            source=source,
        )

        # Step 2: Duplicate check
        is_dup = await self.check_duplicate(dedup_hash)
        if is_dup:
            confidence = Confidence(ref_data.get("confidence", "medium"))
            return GateResult(
                decision=GateDecision.DUPLICATE,
                dedup_hash=dedup_hash,
                confidence=confidence,
                range_validated=True,
            )

        # Step 3: Range validation
        ranges = self._mapping_loader.load()
        value = float(ref_data.get("value", 0))
        range_result = validate_range(property_name, value, ranges)

        # Step 4: Confidence routing
        confidence = Confidence(ref_data.get("confidence", "medium"))
        decision, status = self._route_confidence(confidence, range_result.is_valid)

        return GateResult(
            decision=decision,
            dedup_hash=dedup_hash,
            confidence=confidence,
            range_validated=range_result.is_valid,
            range_detail=range_result,
        )

    async def process_bulk(
        self,
        values: list[dict[str, Any]],
    ) -> BulkGateResult:
        """Process multiple reference values through the quality gate."""
        accepted: list[GateResult] = []
        rejected: list[GateResult] = []
        duplicates: list[GateResult] = []

        for ref_data in values:
            result = await self.process(ref_data)

            if result.decision == GateDecision.DUPLICATE:
                duplicates.append(result)
            elif result.should_stage:
                accepted.append(result)
            else:
                rejected.append(result)

        return BulkGateResult(
            accepted=accepted,
            rejected=rejected,
            duplicates=duplicates,
        )

    async def stage_record(
        self,
        ref_data: dict[str, Any],
        gate_result: GateResult,
        fill_batch_id: UUID | None = None,
    ) -> RefGapFillStaging:
        """Insert a quality-gated record into the staging table.

        Caller is responsible for committing the session.
        """
        _, status = self._route_confidence(
            gate_result.confidence,
            gate_result.range_validated,
        )

        record = RefGapFillStaging(
            element_system=str(ref_data.get("element_system", "")),
            phase=ref_data.get("phase"),
            property_name=str(
                ref_data.get("property", ref_data.get("property_name", ""))
            ),
            value=float(ref_data.get("value", 0)),
            unit=str(ref_data.get("unit", "")),
            method=ref_data.get("method"),
            source=str(ref_data.get("source", "")),
            source_doi=ref_data.get("source_doi"),
            uncertainty=ref_data.get("uncertainty"),
            temperature=ref_data.get("temperature"),
            confidence=gate_result.confidence,
            dedup_hash=gate_result.dedup_hash,
            range_validated=gate_result.range_validated,
            status=status,
            cache_level=ref_data.get("cache_level"),
            fill_batch_id=fill_batch_id,
        )

        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)

        return record
