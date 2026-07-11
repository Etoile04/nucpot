"""Realistic mock data for NFM MCP tools (Phase A).

All data uses realistic nuclear fuel materials domain values:
UO2, Zircaloy-4, SS-316, SiC, FeCrAl, and related
properties, sources, potentials, and ontology concepts.
"""

from __future__ import annotations

import uuid

# ── Materials ──────────────────────────────────────────────────

MATERIALS: list[dict[str, object]] = [
    {
        "id": "UO2",
        "name": "Uranium Dioxide",
        "composition": "UO2",
        "material_type": "fuel",
        "crystal_structure": "Fluorite (Fm-3m)",
        "density_gcc": 10.97,
        "melting_point_k": 3138,
        "property_count": 12,
        "description": (
            "Primary nuclear fuel material used in most commercial "
            "light water reactors worldwide."
        ),
    },
    {
        "id": "Zircaloy-4",
        "name": "Zircaloy-4",
        "composition": "Zr-1.5Sn-0.2Fe-0.1Cr",
        "material_type": "cladding",
        "crystal_structure": "HCP (Mg-type)",
        "density_gcc": 6.55,
        "melting_point_k": 2123,
        "property_count": 9,
        "description": (
            "Zirconium alloy used as fuel cladding in PWR and BWR reactors. "
            "Excellent corrosion resistance and low neutron absorption."
        ),
    },
    {
        "id": "SS-316",
        "name": "Stainless Steel 316",
        "composition": "Fe-17Cr-12Ni-2.5Mo",
        "material_type": "structural",
        "crystal_structure": "FCC (Austenite)",
        "density_gcc": 7.99,
        "melting_point_k": 1723,
        "property_count": 8,
        "description": (
            "Austenitic stainless steel used for reactor internal "
            "structural components and piping."
        ),
    },
    {
        "id": "SiC",
        "name": "Silicon Carbide",
        "composition": "SiC",
        "material_type": "cladding",
        "crystal_structure": "Hexagonal (6H-SiC)",
        "density_gcc": 3.21,
        "melting_point_k": 3103,
        "property_count": 7,
        "description": (
            "Advanced ceramic cladding material under development "
            "for accident-tolerant fuel (ATF) concepts."
        ),
    },
    {
        "id": "FeCrAl",
        "name": "Iron-Chromium-Aluminum (FeCrAl)",
        "composition": "Fe-13Cr-5Al",
        "material_type": "cladding",
        "crystal_structure": "BCC (Ferritic)",
        "density_gcc": 7.20,
        "melting_point_k": 1811,
        "property_count": 6,
        "description": (
            "Ferritic alloy with excellent oxidation resistance. "
            "Candidate ATF cladding and BWR channel box material."
        ),
    },
]

# ── Properties ─────────────────────────────────────────────────

PROPERTIES: list[dict[str, object]] = [
    {
        "id": "prop-001",
        "material_id": "UO2",
        "property_name": "thermal_conductivity",
        "value": 3.5,
        "unit": "W/(m·K)",
        "temperature_k": 600,
        "temperature_range_k": [300, 1500],
        "uncertainty_pct": 5.0,
        "source_id": "src-finkelstein-2001",
        "condition": "95% theoretical density",
    },
    {
        "id": "prop-002",
        "material_id": "UO2",
        "property_name": "thermal_conductivity",
        "value": 2.1,
        "unit": "W/(m·K)",
        "temperature_k": 1500,
        "temperature_range_k": [300, 1500],
        "uncertainty_pct": 8.0,
        "source_id": "src-finkelstein-2001",
        "condition": "95% theoretical density",
    },
    {
        "id": "prop-003",
        "material_id": "UO2",
        "property_name": "density",
        "value": 10.97,
        "unit": "g/cm³",
        "temperature_k": 300,
        "temperature_range_k": [300, 3000],
        "uncertainty_pct": 0.1,
        "source_id": "src-iaea-tdb",
        "condition": "stoichiometric, unirradiated",
    },
    {
        "id": "prop-004",
        "material_id": "Zircaloy-4",
        "property_name": "yield_strength",
        "value": 380,
        "unit": "MPa",
        "temperature_k": 573,
        "temperature_range_k": [300, 800],
        "uncertainty_pct": 10.0,
        "source_id": "src-iaea-tdb",
        "condition": "annealed, 1% strain rate",
    },
    {
        "id": "prop-005",
        "material_id": "Zircaloy-4",
        "property_name": "thermal_conductivity",
        "value": 16.0,
        "unit": "W/(m·K)",
        "temperature_k": 573,
        "temperature_range_k": [300, 1000],
        "uncertainty_pct": 5.0,
        "source_id": "src-iaea-tdb",
        "condition": "unirradiated",
    },
    {
        "id": "prop-006",
        "material_id": "SS-316",
        "property_name": "Young_modulus",
        "value": 193,
        "unit": "GPa",
        "temperature_k": 300,
        "temperature_range_k": [300, 1200],
        "uncertainty_pct": 3.0,
        "source_id": "src-asme-sec2",
        "condition": "solution annealed",
    },
    {
        "id": "prop-007",
        "material_id": "SS-316",
        "property_name": "thermal_expansion",
        "value": 16.5e-6,
        "unit": "1/K",
        "temperature_k": 573,
        "temperature_range_k": [300, 1000],
        "uncertainty_pct": 5.0,
        "source_id": "src-asme-sec2",
        "condition": "solution annealed",
    },
    {
        "id": "prop-008",
        "material_id": "SiC",
        "property_name": "thermal_conductivity",
        "value": 120.0,
        "unit": "W/(m·K)",
        "temperature_k": 300,
        "temperature_range_k": [300, 1600],
        "uncertainty_pct": 10.0,
        "source_id": "src-kanzaki-2019",
        "condition": "CVD, high purity",
    },
    {
        "id": "prop-009",
        "material_id": "FeCrAl",
        "property_name": "yield_strength",
        "value": 310,
        "unit": "MPa",
        "temperature_k": 300,
        "temperature_range_k": [300, 800],
        "uncertainty_pct": 10.0,
        "source_id": "src-rebak-2018",
        "condition": "annealed",
    },
    {
        "id": "prop-010",
        "material_id": "FeCrAl",
        "property_name": "thermal_expansion",
        "value": 13.5e-6,
        "unit": "1/K",
        "temperature_k": 573,
        "temperature_range_k": [300, 1000],
        "uncertainty_pct": 5.0,
        "source_id": "src-rebak-2018",
        "condition": "annealed",
    },
]

# ── Sources ─────────────────────────────────────────────────────

SOURCES: list[dict[str, object]] = [
    {
        "id": "src-finkelstein-2001",
        "authors": "J.K. Finkelstein",
        "title": "Thermal conductivity of UO2 by the laser flash method",
        "journal": "Journal of Nuclear Materials",
        "year": 2001,
        "volume": "288",
        "pages": "89-97",
        "doi": "10.1016/S0022-3115(00)00322-7",
        "source_type": "journal",
        "citation_count": 245,
    },
    {
        "id": "src-iaea-tdb",
        "authors": "IAEA",
        "title": "Thermophysical Properties Database of Materials for Light Water "
        "Reactors",
        "journal": "IAEA-TECDOC (Tecdoc-1496)",
        "year": 2006,
        "volume": None,
        "pages": None,
        "doi": "10.1016/j.nucengdes.2016.04.004",
        "source_type": "report",
        "citation_count": 512,
    },
    {
        "id": "src-asme-sec2",
        "authors": "ASME Boiler and Pressure Vessel Code",
        "title": "Section II — Materials: Properties and Selection",
        "journal": "ASME BPVC Section II Part D",
        "year": 2023,
        "volume": None,
        "pages": None,
        "doi": None,
        "source_type": "handbook",
        "citation_count": 89,
    },
    {
        "id": "src-kanzaki-2019",
        "authors": "K. Kanzaki, T. Yano, H. Hyuga",
        "title": "High-thermal-conductivity SiC ceramics for "
        "nuclear applications",
        "journal": "Ceramics International",
        "year": 2019,
        "volume": "45",
        "pages": "13513-13519",
        "doi": "10.1016/j.ceramint.2019.05.051",
        "source_type": "journal",
        "citation_count": 67,
    },
    {
        "id": "src-rebak-2018",
        "authors": "T. Rebak, B. Pint, D. Teruya",
        "title": "FeCrAl alloys for nuclear energy applications",
        "journal": "Metallurgical and Materials Transactions A",
        "year": 2018,
        "volume": "49",
        "pages": "2253-2262",
        "doi": "10.1007/s11661-018-4617-0",
        "source_type": "journal",
        "citation_count": 134,
    },
]

# ── Potentials ────────────────────────────────────────────────────

POTENTIALS: list[dict[str, object]] = [
    {
        "id": "pot-001",
        "material_id": "UO2",
        "model_name": "FINK-LUCUTA2",
        "potential_type": "Gibbs_energy",
        "expression": "G(T) = G⁰ + a·T·ln(T) + b·T² + c·T³",
        "valid_range_k": [298.15, 3138],
        "coefficients": {
            "G0": -1087400.0,
            "a": -265.4,
            "b": 0.0895,
            "c": -1.5e-5,
        },
        "description": "Gibbs energy of UO2 from FINK-LUCUTA2 "
        "assessment (2004 update)",
    },
    {
        "id": "pot-002",
        "material_id": "UO2",
        "model_name": "FINK-LUCUTA2",
        "potential_type": "Cp",
        "expression": "Cp(T) = c₁ + c₂·T + c₃·T⁻² + c₄·T⁻³",
        "valid_range_k": [298.15, 3138],
        "coefficients": {
            "c1": 52.174,
            "c2": 8.889,
            "c3": -8.527e5,
            "c4": -1.864e8,
        },
        "description": "Heat capacity of UO2 (stoichiometric)",
    },
    {
        "id": "pot-003",
        "material_id": "UO2",
        "model_name": "FINK-LUCUTA2",
        "potential_type": "enthalpy",
        "expression": "H(T) = H⁰ + a·T + b·T² + c·T³ + d·T⁻¹",
        "valid_range_k": [298.15, 3138],
        "coefficients": {
            "H0": -1176595.0,
            "a": -265.4,
            "b": 0.0895,
            "c": -1.5e-5,
            "d": 4.199e6,
        },
        "description": "Enthalpy of formation of UO2",
    },
    {
        "id": "pot-004",
        "material_id": "Zircaloy-4",
        "model_name": "ZRY-PHASE",
        "potential_type": "Gibbs_energy",
        "expression": "G(T) = G_α(T)·(1-x) + G_β(T)·x + Gₘ(x, T)",
        "valid_range_k": [298.15, 2123],
        "coefficients": {
            "G_alpha0": -7050.0,
            "G_beta0": -7020.0,
            "G_excess": 4500.0,
        },
        "description": "α-β phase equilibrium for Zircaloy-4",
    },
]

# ── Ontology ─────────────────────────────────────────────────────

ONTOLOGY: list[dict[str, object]] = [
    {
        "id": "onto-root",
        "label": "Nuclear Materials",
        "entity_type": "root",
        "parent_id": None,
        "description": "Root concept of the NFM ontology",
        "children_count": 4,
    },
    {
        "id": "onto-fuel",
        "label": "Fuel Materials",
        "entity_type": "material_category",
        "parent_id": "onto-root",
        "description": "Materials used as nuclear reactor fuel",
        "children_count": 3,
    },
    {
        "id": "onto-cladding",
        "label": "Cladding Materials",
        "entity_type": "material_category",
        "parent_id": "onto-root",
        "description": "Materials used for fuel rod cladding",
        "children_count": 3,
    },
    {
        "id": "onto-structural",
        "label": "Structural Materials",
        "entity_type": "material_category",
        "parent_id": "onto-root",
        "description": "Materials for reactor structural support",
        "children_count": 2,
    },
    {
        "id": "onto-coolant",
        "label": "Coolant Materials",
        "entity_type": "material_category",
        "parent_id": "onto-root",
        "description": "Materials used as reactor coolant",
        "children_count": 2,
    },
    {
        "id": "onto-uo2",
        "label": "UO2 (Uranium Dioxide)",
        "entity_type": "material",
        "parent_id": "onto-fuel",
        "description": "Primary ceramic nuclear fuel",
        "children_count": 5,
    },
    {
        "id": "onto-uo2-pellet",
        "label": "Fuel Pellet",
        "entity_type": "component",
        "parent_id": "onto-uo2",
        "description": "Cylindrical ceramic pellet of UO2",
        "children_count": 2,
    },
    {
        "id": "onto-zry4",
        "label": "Zircaloy-4",
        "entity_type": "material",
        "parent_id": "onto-cladding",
        "description": "Zirconium-based cladding alloy",
        "children_count": 4,
    },
    {
        "id": "onto-sic",
        "label": "SiC (Silicon Carbide)",
        "entity_type": "material",
        "parent_id": "onto-cladding",
        "description": "Ceramic ATF cladding material",
        "children_count": 3,
    },
    {
        "id": "onto-fecral",
        "label": "FeCrAl (Iron-Chromium-Aluminum)",
        "entity_type": "material",
        "parent_id": "onto-cladding",
        "description": "Ferritic alloy ATF cladding",
        "children_count": 3,
    },
    {
        "id": "onto-thermal-cond",
        "label": "Thermal Conductivity",
        "entity_type": "property",
        "parent_id": "onto-uo2",
        "description": "Ability to conduct heat",
        "children_count": 0,
    },
    {
        "id": "onto-density",
        "label": "Density",
        "entity_type": "property",
        "parent_id": "onto-uo2",
        "description": "Mass per unit volume",
        "children_count": 0,
    },
]

# ── Knowledge Graph ─────────────────────────────────────────────

KG_NODES: list[dict[str, object]] = [
    {"id": "kg-UO2", "label": "UO2", "entity_type": "material"},
    {"id": "kg-Zry4", "label": "Zircaloy-4", "entity_type": "material"},
    {"id": "kg-SS316", "label": "SS-316", "entity_type": "material"},
    {"id": "kg-SiC", "label": "SiC", "entity_type": "material"},
    {"id": "kg-FeCrAl", "label": "FeCrAl", "entity_type": "material"},
    {
        "id": "kg-tc-UO2",
        "label": "thermal_conductivity(UO2)",
        "entity_type": "property",
    },
    {
        "id": "kg-dens-UO2",
        "label": "density(UO2)",
        "entity_type": "property",
    },
    {"id": "kg-fink", "label": "FINK-LUCUTA2", "entity_type": "model"},
    {
        "id": "kg-finkelstein", "label": "Finkelstein (2001)", "entity_type": "source"},
    {"id": "kg-iaea", "label": "IAEA TDB (2006)", "entity_type": "source"},
]

KG_EDGES: list[dict[str, object]] = [
    {
        "source": "kg-UO2",
        "target": "kg-tc-UO2",
        "relationship": "has_property",
    },
    {
        "source": "kg-UO2",
        "target": "kg-dens-UO2",
        "relationship": "has_property",
    },
    {
        "source": "kg-tc-UO2",
        "target": "kg-fink",
        "relationship": "modelled_by",
    },
    {
        "source": "kg-tc-UO2",
        "target": "kg-finkelstein",
        "relationship": "sourced_from",
    },
    {
        "source": "kg-dens-UO2",
        "target": "kg-iaea",
        "relationship": "sourced_from",
    },
    {
        "source": "kg-Zry4",
        "target": "kg-tc-UO2",
        "relationship": "compared_with",
    },
]

# ── Extraction Jobs ─────────────────────────────────────────────

EXTRACTION_JOBS: dict[str, dict[str, object]] = {
    "job-mock-001": {
        "job_id": "job-mock-001",
        "source_id": "https://example.com/papers/finkelstein-2001.pdf",
        "material_id": "UO2",
        "status": "completed",
        "progress": 100,
        "stage": "property_insertion",
        "started_at": "2025-01-15T10:00:00Z",
        "completed_at": "2025-01-15T10:02:30Z",
        "entities_extracted": 14,
        "properties_extracted": 8,
        "error": None,
    },
    "job-mock-002": {
        "job_id": "job-mock-002",
        "source_id": "https://example.com/papers/iaea-tdb.pdf",
        "material_id": "Zircaloy-4",
        "status": "completed",
        "progress": 100,
        "stage": "property_insertion",
        "started_at": "2025-01-16T14:00:00Z",
        "completed_at": "2025-01-16T14:05:00Z",
        "entities_extracted": 9,
        "properties_extracted": 6,
        "error": None,
    },
    "job-mock-003": {
        "job_id": "job-mock-003",
        "source_id": "https://example.com/papers/kanzaki-2019.pdf",
        "material_id": "SiC",
        "status": "running",
        "progress": 65,
        "stage": "property_extraction",
        "started_at": "2025-01-17T09:00:00Z",
        "completed_at": None,
        "entities_extracted": 5,
        "properties_extracted": 3,
        "error": None,
    },
}


def generate_job_id() -> str:
    """Generate a unique mock job ID."""
    return f"job-{uuid.uuid4().hex[:8]}"
