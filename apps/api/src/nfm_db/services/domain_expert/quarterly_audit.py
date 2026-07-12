"""Quarterly Audit Workflow (NFM-98).

Automated quarterly quality audit for P0 safety-critical reference data:
1. Query all P0 systems (U, UO₂, Zr, Fe, U-Zr)
2. Run quality checks:
   a. Uncertainty coverage — % of P0 refs with uncertainty estimates
   b. Recent verification — when was each ref last verified
   c. Conflict detection — refs with conflicting sources
3. Generate audit report with findings
4. Flag issues by severity (critical/high/medium/low)
5. Generate structured audit report
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# P0 systems and core properties
# ---------------------------------------------------------------------------

P0_SYSTEMS: tuple[str, ...] = ("U", "UO2", "Zr", "Fe", "U-Zr")

P0_CORE_PROPERTIES: tuple[str, ...] = (
    "lattice_constant",
    "cohesive_energy",
    "bulk_modulus",
    "elastic_constants",
    "thermal_expansion",
)

# ---------------------------------------------------------------------------
# Audit types
# ---------------------------------------------------------------------------


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckType(str, Enum):
    UNCERTAINTY_COVERAGE = "uncertainty_coverage"
    RECENT_VERIFICATION = "recent_verification"
    CONFLICT_DETECTION = "conflict_detection"
    P0_COMPLETENESS = "p0_completeness"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditConfig:
    """Configuration for quarterly audit execution."""

    quarter: str  # e.g., "2026-Q2"
    start_date: datetime
    end_date: datetime
    p0_systems: tuple[str, ...] = P0_SYSTEMS
    core_properties: tuple[str, ...] = P0_CORE_PROPERTIES
    min_uncertainty_coverage: float = 0.90  # 90% minimum for P0
    max_days_since_verification: int = 90  # Must be verified within last quarter
    max_conflict_threshold: int = 3  # Max conflicting sources before flagging


@dataclass(frozen=True)
class AuditFinding:
    """A single finding from the quarterly audit."""

    finding_id: UUID = field(default_factory=uuid4)
    severity: FindingSeverity = FindingSeverity.LOW
    check_type: CheckType = CheckType.UNCERTAINTY_COVERAGE
    element_system: str = ""
    property_name: str = ""
    description: str = ""
    recommendation: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditReport:
    """Complete quarterly audit report."""

    config: AuditConfig
    report_id: UUID = field(default_factory=uuid4)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    findings: tuple[AuditFinding, ...] = ()
    summary: str = ""
    overall_health: str = "unknown"  # "healthy", "needs_attention", "critical"
    p0_uncertainty_coverage: dict[str, float] = field(default_factory=dict)
    verification_freshness: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------


def _check_uncertainty_coverage(
    refs_by_system: dict[str, list[dict[str, Any]]],
    config: AuditConfig,
) -> list[AuditFinding]:
    """Check what percentage of P0 references have uncertainty estimates."""
    findings: list[AuditFinding] = []
    coverage: dict[str, float] = {}

    for system in config.p0_systems:
        refs = refs_by_system.get(system, [])
        if not refs:
            continue

        with_uncertainty = sum(1 for r in refs if r.get("uncertainty") is not None)
        total = len(refs)
        pct = with_uncertainty / total if total > 0 else 1.0
        coverage[system] = pct

        if pct < config.min_uncertainty_coverage:
            findings.append(
                AuditFinding(
                    severity=(FindingSeverity.CRITICAL if pct < 0.70 else FindingSeverity.HIGH),
                    check_type=CheckType.UNCERTAINTY_COVERAGE,
                    element_system=system,
                    description=(
                        f"Uncertainty coverage {pct:.0%} for {system} "
                        f"(target: {config.min_uncertainty_coverage:.0%})"
                    ),
                    recommendation=(
                        f"Prioritize uncertainty estimation for "
                        f"{total - with_uncertainty} references in {system}"
                    ),
                    metrics={
                        "total_refs": total,
                        "with_uncertainty": with_uncertainty,
                        "coverage_pct": round(pct, 3),
                        "target": config.min_uncertainty_coverage,
                    },
                )
            )

    return findings


def _check_recent_verification(
    refs_by_system: dict[str, list[dict[str, Any]]],
    config: AuditConfig,
) -> list[AuditFinding]:
    """Check that P0 references have been verified recently."""
    findings: list[AuditFinding] = []
    cutoff = config.end_date - timedelta(days=config.max_days_since_verification)

    for system in config.p0_systems:
        refs = refs_by_system.get(system, [])
        stale_refs: list[str] = []

        for ref in refs:
            verified_at = ref.get("verified_at")
            if verified_at is None:
                stale_refs.append(f"{ref.get('property_name', 'unknown')} (never verified)")
            elif isinstance(verified_at, str):
                try:
                    vdate = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
                    if vdate < cutoff:
                        days_ago = (config.end_date - vdate).days
                        stale_refs.append(
                            f"{ref.get('property_name', 'unknown')} ({days_ago}d ago)"
                        )
                except ValueError:
                    pass

        if stale_refs:
            findings.append(
                AuditFinding(
                    severity=FindingSeverity.HIGH,
                    check_type=CheckType.RECENT_VERIFICATION,
                    element_system=system,
                    description=(f"{len(stale_refs)} stale verifications in {system}"),
                    recommendation=(
                        f"Re-verify: {', '.join(stale_refs[:5])}"
                        f"{'...' if len(stale_refs) > 5 else ''}"
                    ),
                    metrics={
                        "stale_count": len(stale_refs),
                        "max_days": config.max_days_since_verification,
                    },
                )
            )

    return findings


def _check_conflicts(
    refs_by_system: dict[str, list[dict[str, Any]]],
    config: AuditConfig,
) -> list[AuditFinding]:
    """Detect references with conflicting sources."""
    findings: list[AuditFinding] = []

    for system in config.p0_systems:
        refs = refs_by_system.get(system, [])
        # Group by property
        by_property: dict[str, list[dict[str, Any]]] = {}
        for ref in refs:
            prop = ref.get("property_name", "unknown")
            by_property.setdefault(prop, []).append(ref)

        for prop, prop_refs in by_property.items():
            if len(prop_refs) < 2:
                continue

            # Check for value dispersion > 20%
            values = [r["value"] for r in prop_refs if r.get("value") is not None]
            if len(values) < 2:
                continue

            mean_val = sum(values) / len(values)
            max_dev = (
                max(abs(v - mean_val) / abs(mean_val) for v in values)
                if mean_val != 0
                else float("inf")
            )

            if max_dev > 0.20:  # 20% deviation
                findings.append(
                    AuditFinding(
                        severity=FindingSeverity.MEDIUM,
                        check_type=CheckType.CONFLICT_DETECTION,
                        element_system=system,
                        property_name=prop,
                        description=(
                            f"Conflicting values for {system} {prop}: "
                            f"dispersion {max_dev:.0%} across {len(values)} sources"
                        ),
                        recommendation=(
                            "Review sources and select primary value; "
                            "document rationale in verification note"
                        ),
                        metrics={
                            "source_count": len(values),
                            "value_range": (
                                min(values),
                                max(values),
                            ),
                            "dispersion_pct": round(max_dev, 3),
                        },
                    )
                )

    return findings


def _check_p0_completeness(
    refs_by_system: dict[str, list[dict[str, Any]]],
    config: AuditConfig,
) -> list[AuditFinding]:
    """Check that all P0 core properties are covered for each system."""
    findings: list[AuditFinding] = []

    for system in config.p0_systems:
        refs = refs_by_system.get(system, [])
        covered_props = {r.get("property_name") for r in refs if r.get("property_name")}
        missing = set(config.core_properties) - covered_props

        if missing:
            findings.append(
                AuditFinding(
                    severity=(
                        FindingSeverity.CRITICAL if len(missing) >= 3 else FindingSeverity.HIGH
                    ),
                    check_type=CheckType.P0_COMPLETENESS,
                    element_system=system,
                    description=(
                        f"Missing {len(missing)} P0 core properties for {system}: "
                        f"{', '.join(sorted(missing))}"
                    ),
                    recommendation=(
                        f"Gap-fill missing properties for {system}: {', '.join(sorted(missing))}"
                    ),
                    metrics={
                        "covered": len(covered_props),
                        "expected": len(config.core_properties),
                        "missing": sorted(missing),
                    },
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _generate_summary(findings: tuple[AuditFinding, ...]) -> str:
    """Generate a human-readable audit summary."""
    critical = sum(1 for f in findings if f.severity == FindingSeverity.CRITICAL)
    high = sum(1 for f in findings if f.severity == FindingSeverity.HIGH)
    medium = sum(1 for f in findings if f.severity == FindingSeverity.MEDIUM)
    low = sum(1 for f in findings if f.severity == FindingSeverity.LOW)

    if critical > 0:
        overall = f"CRITICAL: {critical} critical finding(s) require immediate attention. "
    elif high > 3:
        overall = f"NEEDS ATTENTION: {high} high-severity findings. "
    elif high > 0:
        overall = f"MODERATE: {high} high-severity finding(s). "
    else:
        overall = "HEALTHY: No critical or high-severity findings. "

    details = (
        f"Total findings: {len(findings)} "
        f"({critical} critical, {high} high, {medium} medium, {low} low)"
    )
    return overall + details


def _compute_overall_health(findings: tuple[AuditFinding, ...]) -> str:
    """Compute overall health status from findings."""
    critical = sum(1 for f in findings if f.severity == FindingSeverity.CRITICAL)
    high = sum(1 for f in findings if f.severity == FindingSeverity.HIGH)

    if critical > 0:
        return "critical"
    if high > 2:
        return "needs_attention"
    return "healthy"


# ---------------------------------------------------------------------------
# Main workflow entry point
# ---------------------------------------------------------------------------


def run_quarterly_audit(
    config: AuditConfig,
    *,
    refs_by_system: dict[str, list[dict[str, Any]]] | None = None,
) -> AuditReport:
    """Run the full quarterly audit workflow.

    Args:
        config: Audit configuration (quarter, date range, thresholds).
        refs_by_system: Pre-queried reference data grouped by element_system.
            When absent, the audit runs with empty data (reports missing data).

    Returns:
        AuditReport with all findings, metrics, and summary.
    """
    logger.info(
        "Running quarterly audit for %s (%s — %s)",
        config.quarter,
        config.start_date.date(),
        config.end_date.date(),
    )

    refs = refs_by_system or {}
    all_findings: list[AuditFinding] = []

    # Check 1: Uncertainty coverage
    uncertainty_findings = _check_uncertainty_coverage(refs, config)
    all_findings.extend(uncertainty_findings)
    logger.info("Uncertainty coverage check: %d findings", len(uncertainty_findings))

    # Check 2: Recent verification
    verification_findings = _check_recent_verification(refs, config)
    all_findings.extend(verification_findings)
    logger.info("Recent verification check: %d findings", len(verification_findings))

    # Check 3: Conflict detection
    conflict_findings = _check_conflicts(refs, config)
    all_findings.extend(conflict_findings)
    logger.info("Conflict detection check: %d findings", len(conflict_findings))

    # Check 4: P0 completeness
    completeness_findings = _check_p0_completeness(refs, config)
    all_findings.extend(completeness_findings)
    logger.info("P0 completeness check: %d findings", len(completeness_findings))

    # Compile report
    findings_tuple = tuple(all_findings)
    passed = 4 - len({f.check_type for f in findings_tuple})
    failed = len({f.check_type for f in findings_tuple})

    # Compute coverage metrics
    p0_coverage: dict[str, float] = {}
    for system in config.p0_systems:
        sys_refs = refs.get(system, [])
        if sys_refs:
            with_unc = sum(1 for r in sys_refs if r.get("uncertainty") is not None)
            p0_coverage[system] = round(with_unc / len(sys_refs), 3)
        else:
            p0_coverage[system] = 0.0

    report = AuditReport(
        config=config,
        total_checks=4,
        passed=passed,
        failed=failed,
        findings=findings_tuple,
        summary=_generate_summary(findings_tuple),
        overall_health=_compute_overall_health(findings_tuple),
        p0_uncertainty_coverage=p0_coverage,
        verification_freshness={},  # Populated by caller with DB data
    )

    logger.info(
        "Audit complete: %s — %d findings, health=%s",
        config.quarter,
        len(findings_tuple),
        report.overall_health,
    )

    return report
