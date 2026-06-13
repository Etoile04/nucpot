"""Tests for Quarterly Audit Workflow (NFM-98).

Tests cover:
- Happy path: healthy P0 systems with good coverage
- Edge cases: missing uncertainty, stale verifications, conflicts
- Severity classification
- Report generation
"""

from datetime import datetime, timezone, timedelta

import pytest

from nfm_db.services.domain_expert.quarterly_audit import (
    AuditConfig,
    AuditReport,
    CheckType,
    FindingSeverity,
    P0_CORE_PROPERTIES,
    P0_SYSTEMS,
    run_quarterly_audit,
)


@pytest.mark.unit
class TestQuarterlyAuditWorkflow:
    """Test the quarterly audit workflow end-to-end."""

    def test_healthy_audit_with_no_findings(self) -> None:
        """Audit with healthy data — no critical findings."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        # Helper to create a complete set of properties for a system
        def make_complete_props(system: str) -> list[dict[str, object]]:
            base_date = "2026-05-15T00:00:00Z"
            return [
                {
                    "property_name": "lattice_constant",
                    "value": 2.85 if system == "U" else 5.45,
                    "unit": "Å",
                    "uncertainty": 0.01,
                    "verified_at": base_date,
                },
                {
                    "property_name": "cohesive_energy",
                    "value": 5.0,
                    "unit": "eV/atom",
                    "uncertainty": 0.1,
                    "verified_at": base_date,
                },
                {
                    "property_name": "bulk_modulus",
                    "value": 100.0,
                    "unit": "GPa",
                    "uncertainty": 5.0,
                    "verified_at": base_date,
                },
                {
                    "property_name": "elastic_constants",
                    "value": 100.0,
                    "unit": "GPa",
                    "uncertainty": 10.0,
                    "verified_at": base_date,
                },
                {
                    "property_name": "thermal_expansion",
                    "value": 12.0,
                    "unit": "10⁻⁶/K",
                    "uncertainty": 1.0,
                    "verified_at": base_date,
                },
            ]

        # All P0 systems have all 5 core properties with uncertainty
        refs_by_system = {
            "U": make_complete_props("U"),
            "UO2": make_complete_props("UO2"),
            "Zr": make_complete_props("Zr"),
            "Fe": make_complete_props("Fe"),
            "U-Zr": make_complete_props("U-Zr"),
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        # All systems have complete properties with uncertainty
        assert result.overall_health == "healthy"
        assert len([f for f in result.findings if f.severity == FindingSeverity.CRITICAL]) == 0

    def test_uncertainty_coverage_below_threshold(self) -> None:
        """Detect low uncertainty coverage on P0 systems."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
            min_uncertainty_coverage=0.90,
        )

        # Only 50% have uncertainty
        refs_by_system = {
            "U": [
                {"property_name": "lattice_constant", "value": 2.85, "unit": "Å", "uncertainty": 0.01},
                {"property_name": "cohesive_energy", "value": 5.0, "unit": "eV/atom"},  # No uncertainty
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        uncertainty_findings = [
            f for f in result.findings if f.check_type == CheckType.UNCERTAINTY_COVERAGE
        ]
        assert len(uncertainty_findings) > 0
        assert uncertainty_findings[0].severity in (
            FindingSeverity.CRITICAL,
            FindingSeverity.HIGH,
        )

    def test_stale_verification_triggers_finding(self) -> None:
        """Detect references not verified within max_days threshold."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
            max_days_since_verification=90,
        )

        # Verification from > 90 days ago
        stale_date = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
        refs_by_system = {
            "Zr": [
                {
                    "property_name": "bulk_modulus",
                    "value": 100.0,
                    "unit": "GPa",
                    "verified_at": stale_date,
                }
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        stale_findings = [
            f for f in result.findings if f.check_type == CheckType.RECENT_VERIFICATION
        ]
        assert len(stale_findings) > 0
        assert "stale" in stale_findings[0].description.lower()

    def test_conflicting_values_detected(self) -> None:
        """Detect conflicting values from multiple sources."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        # Two sources with >20% deviation from mean
        # Values 2.0 and 4.0: mean = 3.0, max deviation = 1.0/3.0 = 33.3%
        refs_by_system = {
            "Fe": [
                {"property_name": "lattice_constant", "value": 2.0, "unit": "Å"},
                {"property_name": "lattice_constant", "value": 4.0, "unit": "Å"},  # >20% deviation
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        # Conflict detection checks for value dispersion > 20%
        conflict_findings = [
            f for f in result.findings if f.check_type == CheckType.CONFLICT_DETECTION
        ]
        assert len(conflict_findings) > 0

    def test_missing_p0_properties_triggers_critical(self) -> None:
        """Missing P0 core properties triggers critical findings."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
            core_properties=P0_CORE_PROPERTIES,
        )

        # Only 2 of 5 core properties covered
        refs_by_system = {
            "U-Zr": [
                {"property_name": "lattice_constant", "value": 3.2, "unit": "Å"},
                {"property_name": "cohesive_energy", "value": 5.0, "unit": "eV/atom"},
                # Missing: bulk_modulus, elastic_constants, thermal_expansion
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        completeness_findings = [
            f for f in result.findings if f.check_type == CheckType.P0_COMPLETENESS
        ]
        assert len(completeness_findings) > 0
        assert completeness_findings[0].severity == FindingSeverity.CRITICAL


@pytest.mark.unit
class TestSeverityClassification:
    """Test finding severity classification logic."""

    def test_critical_severity_for_low_coverage(self) -> None:
        """Coverage < 70% triggers CRITICAL severity."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
            min_uncertainty_coverage=0.90,
        )

        # Only 60% coverage
        refs_by_system = {
            "U": [
                {"property_name": "lattice_constant", "value": 2.85, "unit": "Å", "uncertainty": 0.01},
                {"property_name": "cohesive_energy", "value": 5.0, "unit": "eV/atom"},
                {"property_name": "bulk_modulus", "value": 100.0, "unit": "GPa"},
                {"property_name": "thermal_expansion", "value": 12.0, "unit": "10⁻⁶/K"},
                {"property_name": "elastic_constants", "value": 100.0, "unit": "GPa"},
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        critical_findings = [f for f in result.findings if f.severity == FindingSeverity.CRITICAL]
        assert len(critical_findings) > 0

    def test_high_severity_for_medium_coverage(self) -> None:
        """Coverage 70-90% triggers HIGH severity."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
            min_uncertainty_coverage=0.90,
        )

        # 80% coverage
        refs_by_system = {
            "U": [
                {"property_name": "p1", "value": 1.0, "unit": "Å", "uncertainty": 0.01},
                {"property_name": "p2", "value": 2.0, "unit": "Å", "uncertainty": 0.01},
                {"property_name": "p3", "value": 3.0, "unit": "Å", "uncertainty": 0.01},
                {"property_name": "p4", "value": 4.0, "unit": "Å", "uncertainty": 0.01},
                {"property_name": "p5", "value": 5.0, "unit": "Å"},  # No uncertainty
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        high_findings = [
            f
            for f in result.findings
            if f.severity == FindingSeverity.HIGH
            and f.check_type == CheckType.UNCERTAINTY_COVERAGE
        ]
        assert len(high_findings) > 0


@pytest.mark.unit
class TestReportGeneration:
    """Test audit report generation and structure."""

    def test_report_summary_generation(self) -> None:
        """Generate human-readable summary from findings."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        refs_by_system = {}  # Empty data will trigger findings

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        assert result.summary is not None
        assert len(result.summary) > 0
        assert "2026-Q2" in result.summary or "findings" in result.summary.lower()

    def test_overall_health_computation(self) -> None:
        """Compute overall health status from findings."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        refs_by_system = {}  # Empty data — likely critical findings

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        assert result.overall_health in ("healthy", "needs_attention", "critical")
        # Empty data should yield critical or needs_attention
        assert result.overall_health in ("critical", "needs_attention")

    def test_p0_coverage_metrics_populated(self) -> None:
        """P0 uncertainty coverage metrics are computed."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
            p0_systems=P0_SYSTEMS,
        )

        refs_by_system = {
            "U": [
                {"property_name": "lattice_constant", "value": 2.85, "unit": "Å", "uncertainty": 0.01},
            ],
            "UO2": [
                {"property_name": "lattice_constant", "value": 5.45, "unit": "Å"},  # No uncertainty
            ],
        }

        result = run_quarterly_audit(config, refs_by_system=refs_by_system)

        assert "U" in result.p0_uncertainty_coverage
        assert "UO2" in result.p0_uncertainty_coverage
        assert result.p0_uncertainty_coverage["U"] == 1.0  # 100%
        assert result.p0_uncertainty_coverage["UO2"] == 0.0  # 0%


@pytest.mark.unit
class TestResultStructure:
    """Test audit result data structure."""

    def test_result_has_all_required_fields(self) -> None:
        """Audit result contains all required fields."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        result = run_quarterly_audit(config)

        assert isinstance(result, AuditReport)
        assert isinstance(result.total_checks, int)
        assert isinstance(result.passed, int)
        assert isinstance(result.failed, int)
        assert isinstance(result.findings, tuple)
        assert result.total_checks == 4


@pytest.mark.unit
class TestFindingStructure:
    """Test individual finding data structure."""

    def test_finding_has_all_required_fields(self) -> None:
        """Individual finding contains all required fields."""
        config = AuditConfig(
            quarter="2026-Q2",
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        result = run_quarterly_audit(config)

        if result.findings:
            finding = result.findings[0]
            assert finding.severity in (
                FindingSeverity.CRITICAL,
                FindingSeverity.HIGH,
                FindingSeverity.MEDIUM,
                FindingSeverity.LOW,
            )
            assert finding.check_type in (
                CheckType.UNCERTAINTY_COVERAGE,
                CheckType.RECENT_VERIFICATION,
                CheckType.CONFLICT_DETECTION,
                CheckType.P0_COMPLETENESS,
            )
            assert finding.description is not None
            assert finding.recommendation is not None
