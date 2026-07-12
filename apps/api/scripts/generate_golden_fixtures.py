#!/usr/bin/env python3
"""Generate golden fixture JSON files for test_golden_regression.py (NFM-553).

This script creates 12 representative paper fixtures covering diverse:
- Property categories (structural, thermal, mechanical, irradiation)
- Material systems (UO2, Zr alloys, steel, SiC, graphite)
- LaTeX patterns (subscripts, superscripts, scientific notation, degrees)
- Range formats (to, between, dash-separated, ±)
- Confidence levels (high, medium, low)

Each fixture runs through the actual pipeline to generate expected outputs,
ensuring golden tests stay in sync with real behavior.

Usage:
    cd apps/api && python scripts/generate_golden_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfm_db.core.extraction_rules import (
    assess_confidence,
    clean_latex,
    parse_value,
)
from nfm_db.services.quality_gate import compute_dedup_hash
from nfm_db.services.v4_mapper import v4_record_to_staging

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "golden"


def _build_expected(input_record: dict[str, Any]) -> dict[str, Any]:
    """Run input through the pipeline and capture all expected outputs."""
    raw_value = str(input_record["value"])

    # parse_value
    parsed = parse_value(raw_value)
    parsed_out: dict[str, Any] = {
        "main_value": parsed.main_value,
    }
    if parsed.uncertainty is not None:
        parsed_out["uncertainty"] = parsed.uncertainty
    if parsed.range is not None:
        parsed_out["range"] = list(parsed.range)

    # clean_latex
    cleaned = clean_latex(raw_value)

    # assess_confidence
    confidence_record: dict[str, Any] = {
        "source_file": input_record.get("source_file", ""),
        "material_name": input_record.get("material_name", ""),
        "property_category": input_record.get("property_category", ""),
        "property": input_record.get("property", ""),
        "value": input_record.get("value", ""),
        "unit": input_record.get("unit", ""),
        "reference": input_record.get("reference", ""),
    }
    if input_record.get("phase"):
        confidence_record["phase"] = input_record["phase"]
    if input_record.get("conditions"):
        confidence_record["conditions"] = input_record["conditions"]
    confidence = assess_confidence(confidence_record)

    # dedup_hash
    dedup_hash = compute_dedup_hash(
        element_system=input_record.get("material_name", ""),
        phase=input_record.get("phase"),
        property_name=input_record.get("property", ""),
        method=None,
        source=input_record.get("reference", ""),
    )

    # v4 staging mapping
    staging = v4_record_to_staging(input_record)

    return {
        "parsed_value": parsed_out,
        "cleaned_value": cleaned,
        "confidence": confidence.value,
        "dedup_hash": dedup_hash,
        "staging": staging,
    }


def _build_fixture(
    fixture_id: str,
    description: str,
    source_file: str,
    property_category: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a complete golden fixture dict."""
    return {
        "id": fixture_id,
        "description": description,
        "source_file": source_file,
        "property_category": property_category,
        "records": records,
    }


def _u02_density_fixture() -> dict[str, Any]:
    """UO2 density - plain values and LaTeX."""
    records = [
        {
            "id": "u02-density-plain",
            "input": {
                "source_file": "zhang_2020_uo2_density.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "密度",
                "property": "密度",
                "value": "10.96",
                "unit": "g/cm³",
                "context": "sintered pellet at 95% TD",
                "reference": "Zhang et al., J. Nucl. Mater. 2020",
            },
        },
        {
            "id": "u02-density-latex",
            "input": {
                "source_file": "zhang_2020_uo2_density.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "密度",
                "property": "密度",
                "value": "$10.5\\times10^{3}$",
                "unit": "kg/m³",
                "context": "theoretical density",
                "reference": "Zhang et al., J. Nucl. Mater. 2020",
            },
        },
        {
            "id": "u02-density-range",
            "input": {
                "source_file": "zhang_2020_uo2_density.md",
                "material_name": "UO2",
                "composition": "UO2.02",
                "phase": "alpha",
                "element": "U",
                "property_category": "密度",
                "property": "密度",
                "value": "10.4 to 10.97",
                "unit": "g/cm³",
                "context": "stoichiometry variation",
                "reference": "Zhang et al., J. Nucl. Mater. 2020",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "u02-density",
        "UO2 density measurements - plain, LaTeX scientific notation, range",
        "zhang_2020_uo2_density.md",
        "密度",
        records,
    )


def _uo2_thermal_conductivity_fixture() -> dict[str, Any]:
    """UO2 thermal conductivity - scientific notation, conditions."""
    records = [
        {
            "id": "uo2-tc-sci-e",
            "input": {
                "source_file": "fink_2000_uo2_tc.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "热传导率",
                "property": "热导率",
                "value": "3.5e-2",
                "unit": "W/(m·K)",
                "conditions": {"temp_C": 800, "condition_type": "experimental"},
                "context": "stoichiometric UO2 at elevated temperature",
                "reference": "Fink, J. Nucl. Mater. 2000",
            },
        },
        {
            "id": "uo2-tc-latex-times",
            "input": {
                "source_file": "fink_2000_uo2_tc.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "热传导率",
                "property": "热导率",
                "value": "$8.5\\times10^{-3}$",
                "unit": "W/(m·K)",
                "conditions": {"temp_C": 1500, "condition_type": "experimental"},
                "context": "high temperature regime",
                "reference": "Fink, J. Nucl. Mater. 2000",
            },
        },
        {
            "id": "uo2-tc-uncertainty",
            "input": {
                "source_file": "fink_2000_uo2_tc.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "热传导率",
                "property": "热导率",
                "value": "5.0 ± 0.3",
                "unit": "W/(m·K)",
                "conditions": {"temp_C": 300, "condition_type": "experimental"},
                "context": "room temperature measurement",
                "reference": "Fink, J. Nucl. Mater. 2000",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "uo2-thermal-conductivity",
        "UO2 thermal conductivity - scientific notation, LaTeX, uncertainty",
        "fink_2000_uo2_tc.md",
        "热传导率",
        records,
    )


def _zr_mechanical_fixture() -> dict[str, Any]:
    """Zr alloy mechanical properties - yield strength, tensile."""
    records = [
        {
            "id": "zr-yield-strength",
            "input": {
                "source_file": "kim_2018_zr_alloy.md",
                "material_name": "Zr-2.5Nb",
                "phase": "alpha",
                "element": "Zr",
                "property_category": "弹塑性模型",
                "property": "屈服强度",
                "value": "380",
                "unit": "MPa",
                "conditions": {"temp_C": 25, "strain_rate_s1": "0.001"},
                "context": "pressure tube material, longitudinal direction",
                "reference": "Kim et al., J. ASTM Int. 2018",
            },
        },
        {
            "id": "zr-tensile-range",
            "input": {
                "source_file": "kim_2018_zr_alloy.md",
                "material_name": "Zr-2.5Nb",
                "phase": "alpha",
                "element": "Zr",
                "property_category": "弹塑性模型",
                "property": "抗拉强度",
                "value": "500 to 600",
                "unit": "MPa",
                "conditions": {"temp_C": 300, "condition_type": "experimental"},
                "context": "irradiated condition",
                "reference": "Kim et al., J. ASTM Int. 2018",
            },
        },
        {
            "id": "zr-youngs-modulus",
            "input": {
                "source_file": "kim_2018_zr_alloy.md",
                "material_name": "Zr-4",
                "phase": "alpha",
                "element": "Zr",
                "property_category": "弹塑性模型",
                "property": "弹性模量",
                "value": "~95",
                "unit": "GPa",
                "context": "cladding material at RT",
                "reference": "Kim et al., J. ASTM Int. 2018",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "zr-mechanical",
        "Zr alloy mechanical properties - yield strength, tensile range, modulus",
        "kim_2018_zr_alloy.md",
        "弹塑性模型",
        records,
    )


def _uo2_swelling_fixture() -> dict[str, Any]:
    """UO2 irradiation swelling - volume change, dpa."""
    records = [
        {
            "id": "uo2-swelling-plain",
            "input": {
                "source_file": "spino_2020_uo2_swelling.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "辐照肿胀",
                "property": "体积肿胀率",
                "value": "1.5",
                "unit": "%",
                "conditions": {
                    "temp_C": 600,
                    "dpa": "40",
                    "condition_type": "experimental",
                },
                "context": "LWR burnup at 40 dpa",
                "reference": "Spino et al., JNM 2020",
            },
        },
        {
            "id": "uo2-swelling-range",
            "input": {
                "source_file": "spino_2020_uo2_swelling.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "辐照肿胀",
                "property": "体积肿胀率",
                "value": "0.8 to 2.5",
                "unit": "%",
                "conditions": {
                    "temp_C": 500,
                    "dpa": "15",
                    "condition_type": "experimental",
                },
                "context": "intermediate burnup range",
                "reference": "Spino et al., JNM 2020",
            },
        },
        {
            "id": "uo2-swelling-sci",
            "input": {
                "source_file": "spino_2020_uo2_swelling.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "辐照肿胀",
                "property": "密度变化率",
                "value": "3.2e-4",
                "unit": "/dpa",
                "conditions": {
                    "temp_C": 800,
                    "dpa": "80",
                    "condition_type": "irradiation",
                },
                "context": "high burnup high temperature",
                "reference": "Spino et al., JNM 2020",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "uo2-irradiation-swelling",
        "UO2 irradiation swelling - plain, range, scientific notation",
        "spino_2020_uo2_swelling.md",
        "辐照肿胀",
        records,
    )


def _sic_thermal_expansion_fixture() -> dict[str, Any]:
    """SiC thermal expansion - CTE values."""
    records = [
        {
            "id": "sic-cte-plain",
            "input": {
                "source_file": "katoh_2014_sic_cvd.md",
                "material_name": "SiC",
                "composition": "CVD-SiC",
                "phase": "beta",
                "element": "Si",
                "property_category": "热膨胀",
                "property": "热膨胀系数",
                "value": "4.0",
                "unit": "×10⁻⁶/K",
                "conditions": {"temp_C": 800, "condition_type": "experimental"},
                "context": "CVD SiC axial direction",
                "reference": "Katoh et al., JNM 2014",
            },
        },
        {
            "id": "sic-cte-latex-sub",
            "input": {
                "source_file": "katoh_2014_sic_cvd.md",
                "material_name": "SiC",
                "composition": "${SiC}_{f}$/SiC",
                "phase": "beta",
                "element": "Si",
                "property_category": "热膨胀",
                "property": "热膨胀系数",
                "value": "4.5 ± 0.2",
                "unit": "×10⁻⁶/K",
                "conditions": {"temp_C": 1000, "condition_type": "experimental"},
                "context": "composite at high temperature",
                "reference": "Katoh et al., JNM 2014",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "sic-thermal-expansion",
        "SiC thermal expansion - CTE plain and with uncertainty",
        "katoh_2014_sic_cvd.md",
        "热膨胀",
        records,
    )


def _steel_corrosion_fixture() -> dict[str, Any]:
    """Austenitic stainless steel corrosion - weight loss."""
    records = [
        {
            "id": "steel-corrosion-plain",
            "input": {
                "source_file": "was_2014_ss_corrosion.md",
                "material_name": "316SS",
                "composition": "Fe-17Cr-12Ni-2Mo",
                "phase": "gamma",
                "element": "Fe",
                "property_category": "腐蚀",
                "property": "腐蚀速率",
                "value": "2.3",
                "unit": "mg/dm²/day",
                "conditions": {
                    "temp_C": 300,
                    "atmosphere": "PWR primary water",
                    "condition_type": "experimental",
                },
                "context": "PWR normal water chemistry",
                "reference": "Was et al., Corrosion 2014",
            },
        },
        {
            "id": "steel-corrosion-range",
            "input": {
                "source_file": "was_2014_ss_corrosion.md",
                "material_name": "316SS",
                "composition": "Fe-17Cr-12Ni-2Mo",
                "phase": "gamma",
                "element": "Fe",
                "property_category": "腐蚀",
                "property": "氧化膜厚度",
                "value": "50 to 120",
                "unit": "nm",
                "conditions": {
                    "temp_C": 360,
                    "time_h": "5000",
                    "condition_type": "experimental",
                },
                "context": "oxide scale after 5000h exposure",
                "reference": "Was et al., Corrosion 2014",
            },
        },
        {
            "id": "steel-corrosion-low-conf",
            "input": {
                "material_name": "304SS",
                "property_category": "腐蚀",
                "property": "腐蚀速率",
                "value": "5.1",
                "unit": "mg/dm²/day",
                "context": "high temperature water",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "steel-corrosion",
        "316/304SS corrosion - weight loss, oxide thickness range, low confidence",
        "was_2014_ss_corrosion.md",
        "腐蚀",
        records,
    )


def _uo2_specific_heat_fixture() -> dict[str, Any]:
    """UO2 specific heat - temperature-dependent values."""
    records = [
        {
            "id": "uo2-cp-at-300k",
            "input": {
                "source_file": "fink_2000_uo2_cp.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "比热容",
                "property": "比热容",
                "value": "300",
                "unit": "J/(kg·K)",
                "conditions": {"temp_K": 1200, "condition_type": "experimental"},
                "context": "high temperature specific heat",
                "reference": "Fink, Thermochim. Acta 2000",
            },
        },
        {
            "id": "uo2-cp-latex-mu",
            "input": {
                "source_file": "fink_2000_uo2_cp.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "比热容",
                "property": "比热容",
                "value": "80.5",
                "unit": "J/(mol·K)",
                "conditions": {"temp_K": 298, "condition_type": "experimental"},
                "context": "standard conditions",
                "reference": "Fink, Thermochim. Acta 2000",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "uo2-specific-heat",
        "UO2 specific heat - high temp and standard conditions",
        "fink_2000_uo2_cp.md",
        "比热容",
        records,
    )


def _graphite_thermal_conductivity_fixture() -> dict[str, Any]:
    """Nuclear graphite thermal conductivity - anisotropic."""
    records = [
        {
            "id": "graphite-tc-parallel",
            "input": {
                "source_file": "joyce_2019_nbg17.md",
                "material_name": "NBG-17",
                "phase": "graphite",
                "element": "C",
                "property_category": "热传导率",
                "property": "热导率",
                "value": "175",
                "unit": "W/(m·K)",
                "conditions": {"temp_C": 25, "condition_type": "experimental"},
                "context": "parallel to grain direction",
                "reference": "Joyce et al., Carbon 2019",
            },
        },
        {
            "id": "graphite-tc-perpendicular",
            "input": {
                "source_file": "joyce_2019_nbg17.md",
                "material_name": "NBG-17",
                "phase": "graphite",
                "element": "C",
                "property_category": "热传导率",
                "property": "热导率",
                "value": "110 ± 8",
                "unit": "W/(m·K)",
                "conditions": {"temp_C": 25, "condition_type": "experimental"},
                "context": "perpendicular to grain direction",
                "reference": "Joyce et al., Carbon 2019",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "graphite-thermal-conductivity",
        "NBG-17 graphite thermal conductivity - anisotropic values",
        "joyce_2019_nbg17.md",
        "热传导率",
        records,
    )


def _zr_irradiation_creep_fixture() -> dict[str, Any]:
    """Zr alloy irradiation creep - strain rate."""
    records = [
        {
            "id": "zr-creep-rate",
            "input": {
                "source_file": "friday_2018_zr_creep.md",
                "material_name": "Zr-2.5Nb",
                "phase": "alpha",
                "element": "Zr",
                "property_category": "辐照蠕变",
                "property": "蠕变速率",
                "value": "$2.5\\times10^{-7}$",
                "unit": "/s",
                "conditions": {
                    "temp_C": 300,
                    "stress_MPa": 120,
                    "flux_n_m2_s": "3×10^17",
                    "condition_type": "irradiation",
                },
                "context": "in-reactor pressure tube creep",
                "reference": "Friday et al., JNM 2018",
            },
        },
        {
            "id": "zr-creep-strain",
            "input": {
                "source_file": "friday_2018_zr_creep.md",
                "material_name": "Zr-2.5Nb",
                "phase": "alpha",
                "element": "Zr",
                "property_category": "辐照蠕变",
                "property": "蠕变应变",
                "value": "0.5 to 1.2",
                "unit": "%",
                "conditions": {
                    "temp_C": 300,
                    "fluence_n_m2": "5×10^25",
                    "condition_type": "irradiation",
                },
                "context": "axial strain after full bundle irradiation",
                "reference": "Friday et al., JNM 2018",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "zr-irradiation-creep",
        "Zr-2.5Nb irradiation creep - strain rate (LaTeX), strain range",
        "friday_2018_zr_creep.md",
        "辐照蠕变",
        records,
    )


def _steel_hardening_fixture() -> dict[str, Any]:
    """RAFM steel hardening - DBTT shift."""
    records = [
        {
            "id": "eurofer97-hardening",
            "input": {
                "source_file": "aktaa_2019_eurofer97.md",
                "material_name": "EUROFER97",
                "composition": "Fe-9Cr-1W-0.2V-0.08Ta",
                "phase": "martensite",
                "element": "Fe",
                "property_category": "硬化性能",
                "property": "屈服强度增量",
                "value": "250 ± 30",
                "unit": "MPa",
                "conditions": {
                    "temp_C": 25,
                    "dpa": "15",
                    "condition_type": "irradiation",
                },
                "context": "neutron irradiation hardening at 15 dpa",
                "reference": "Aktaa et al., Fus. Eng. Des. 2019",
            },
        },
        {
            "id": "eurofer97-dbtt",
            "input": {
                "source_file": "aktaa_2019_eurofer97.md",
                "material_name": "EUROFER97",
                "composition": "Fe-9Cr-1W-0.2V-0.08Ta",
                "phase": "martensite",
                "element": "Fe",
                "property_category": "硬化性能",
                "property": "韧脆转变温度偏移",
                "value": "80 to 120",
                "unit": "°C",
                "conditions": {
                    "dpa": "15",
                    "condition_type": "irradiation",
                },
                "context": "DBTT shift after neutron irradiation",
                "reference": "Aktaa et al., Fus. Eng. Des. 2019",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "steel-hardening",
        "EUROFER97 hardening - yield strength increment, DBTT shift range",
        "aktaa_2019_eurofer97.md",
        "硬化性能",
        records,
    )


def _sic_mechanical_fixture() -> dict[str, Any]:
    """SiC mechanical properties - flexural strength."""
    records = [
        {
            "id": "sic-flexural-strength",
            "input": {
                "source_file": "katoh_2014_sic_mech.md",
                "material_name": "CVD-SiC",
                "phase": "beta",
                "element": "Si",
                "property_category": "弹塑性模型",
                "property": "弯曲强度",
                "value": "380",
                "unit": "MPa",
                "conditions": {"temp_C": 25, "condition_type": "experimental"},
                "context": "room temperature flexural strength",
                "reference": "Katoh et al., JNM 2014",
            },
        },
        {
            "id": "sic-flexural-strength-ht",
            "input": {
                "source_file": "katoh_2014_sic_mech.md",
                "material_name": "CVD-SiC",
                "phase": "beta",
                "element": "Si",
                "property_category": "弹塑性模型",
                "property": "弯曲强度",
                "value": "250 to 300",
                "unit": "MPa",
                "conditions": {"temp_C": 1200, "condition_type": "experimental"},
                "context": "high temperature strength retention",
                "reference": "Katoh et al., JNM 2014",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "sic-mechanical",
        "CVD-SiC mechanical - flexural strength RT and HT",
        "katoh_2014_sic_mech.md",
        "弹塑性模型",
        records,
    )


def _uo2_material_spec_fixture() -> dict[str, Any]:
    """UO2 material specifications - grain size, porosity."""
    records = [
        {
            "id": "uo2-grain-size",
            "input": {
                "source_file": "rest_2005_uo2_fabrication.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "材料规格/组织信息",
                "property": "晶粒尺寸",
                "value": "8",
                "unit": "μm",
                "context": "sintered pellet",
                "reference": "Rest, JNM 2005",
            },
        },
        {
            "id": "uo2-porosity",
            "input": {
                "source_file": "rest_2005_uo2_fabrication.md",
                "material_name": "UO2",
                "composition": "UO2.00",
                "phase": "alpha",
                "element": "U",
                "property_category": "材料规格/组织信息",
                "property": "孔隙率",
                "value": "approximately 5",
                "unit": "%",
                "context": "as-sintered pellet",
                "reference": "Rest, JNM 2005",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "uo2-material-spec",
        "UO2 material specs - grain size, porosity with approximate",
        "rest_2005_uo2_fabrication.md",
        "材料规格/组织信息",
        records,
    )


def _steel_low_confidence_fixture() -> dict[str, Any]:
    """Minimal record - low confidence path, missing many fields."""
    records = [
        {
            "id": "unknown-steel-yield",
            "input": {
                "material_name": "unknown steel",
                "property": "屈服强度",
                "value": "350",
                "unit": "MPa",
            },
        },
    ]
    for rec in records:
        rec["expected"] = _build_expected(rec["input"])
    return _build_fixture(
        "unknown-steel-low-conf",
        "Unknown steel - minimal fields, low confidence only",
        "unknown_source.md",
        "弹塑性模型",
        records,
    )


def main() -> None:
    """Generate all golden fixture files."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    fixtures = [
        _u02_density_fixture(),
        _uo2_thermal_conductivity_fixture(),
        _zr_mechanical_fixture(),
        _uo2_swelling_fixture(),
        _sic_thermal_expansion_fixture(),
        _steel_corrosion_fixture(),
        _uo2_specific_heat_fixture(),
        _graphite_thermal_conductivity_fixture(),
        _zr_irradiation_creep_fixture(),
        _steel_hardening_fixture(),
        _sic_mechanical_fixture(),
        _uo2_material_spec_fixture(),
        _steel_low_confidence_fixture(),
    ]

    total_records = 0
    for fixture in fixtures:
        path = GOLDEN_DIR / f"{fixture['id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fixture, f, indent=2, ensure_ascii=False)
            f.write("\n")
        record_count = len(fixture["records"])
        total_records += record_count
        print(f"  ✓ {fixture['id']}.json ({record_count} records)")

    print(f"\nGenerated {len(fixtures)} fixtures with {total_records} total records")
    print(f"Output directory: {GOLDEN_DIR}")


if __name__ == "__main__":
    main()
