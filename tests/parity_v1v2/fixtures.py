"""Three representative sample data sets for the V1<->V2 parity harness (NFM-3539).

Per the issue AC: "Executes on >=3 representative sample data sets
(chosen for diversity: short, long, multi-document)".

Each fixture is a *Markdown* document so the V2 ``SectionSegmenter``
produces at least one section per fixture.  The V1 stub path returns
the same three canned UO2 records regardless of input; the V2 path
extracts material properties from the actual content.  Parity is
therefore expected to be *partial* — the harness's job is to surface
and classify the divergence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    """A single sample data set for the parity harness."""

    name: str
    kind: str  # 'short' | 'long' | 'multi-doc'
    text: str


# --- SHORT (single property, single document) -----------------------------
SHORT_TEXT = """\
# UO2 Lattice Parameter Brief

This short note records the lattice constant of stoichiometric UO2 at
ambient temperature.

## Lattice Parameter

FCC lattice constant for UO2: 5.470 angstrom.
"""

# --- LONG (multi-section, multi-property, single document) -----------------
LONG_TEXT = """\
# UO2 Mechanical and Thermal Properties

This long-form note captures a dense property table for UO2 across
several sections.  Designed to stress the section segmenter and entity
extractor.

## Lattice Parameter

FCC lattice constant for UO2: 5.470 angstrom at 300 K.
Heat of formation: -1085 kJ/mol.

## Elastic Moduli

Bulk modulus for UO2 at 300 K: 207.5 GPa.
Shear modulus for UO2 at 300 K: 83.6 GPa.
Young's modulus for UO2 at 300 K: 222.5 GPa.
Poisson ratio for UO2: 0.32.

## Thermal Properties

Thermal conductivity for UO2 at 300 K: 7.5 W/(m*K).
Thermal conductivity for UO2 at 1000 K: 3.5 W/(m*K).
Specific heat capacity for UO2 at 300 K: 0.235 J/(g*K).
Linear thermal expansion coefficient for UO2 at 300 K: 9.7e-6 /K.

## Diffusion

Activation energy for U diffusion in UO2: 3.0 eV.
Pre-exponential factor for U diffusion: 5.0e-5 cm^2/s.

## Defect Formation

Frenkel pair formation energy in UO2: 4.5 eV.
Schottky defect formation energy in UO2: 6.2 eV.

## Melting and Thermodynamics

Melting point of UO2: 3120 K.
Enthalpy of fusion for UO2: 71.0 kJ/mol.
Vapor pressure of UO2 at 2000 K: 1.2e-6 atm.

## Optical and Dielectric

Refractive index of UO2 at 590 nm: 2.35.
Static dielectric constant of UO2: 24.0.
High-frequency dielectric constant of UO2: 5.3.
"""

# --- MULTI_DOC (5 concatenated documents) ----------------------------------
# Each document is short enough to be self-contained; the harness treats the
# concatenation as a single corpus so the V2 chunker / segmenter emit
# multiple sections per document.
_DOC_1 = """\
# Doc 1: UO2 Baseline

UO2 lattice constant: 5.470 angstrom.
UO2 bulk modulus: 207.5 GPa.
"""

_DOC_2 = """\
# Doc 2: MOX Variant

MOX (U0.95Pu0.05)O2 lattice constant: 5.420 angstrom.
MOX thermal conductivity at 1000 K: 2.8 W/(m*K).
"""

_DOC_3 = """\
# Doc 3: Zircaloy-4 Cladding

Zircaloy-4 lattice constant: 3.230 angstrom.
Zircaloy-4 tensile strength at 600 K: 530 MPa.
Zircaloy-4 thermal conductivity at 600 K: 16.5 W/(m*K).
"""

_DOC_4 = """\
# Doc 4: BeO Reflector

BeO lattice constant: 2.698 angstrom.
BeO thermal conductivity at 500 K: 60.0 W/(m*K).
"""

_DOC_5 = """\
# Doc 5: SiC Composite

SiC lattice constant: 4.360 angstrom.
SiC Young's modulus at 300 K: 410.0 GPa.
SiC thermal conductivity at 300 K: 120.0 W/(m*K).
"""

MULTI_DOC_TEXT = "\n\n---\n\n".join(
    (_DOC_1, _DOC_2, _DOC_3, _DOC_4, _DOC_5),
)


SHORT = Fixture(name="short", kind="short", text=SHORT_TEXT)
LONG = Fixture(name="long", kind="long", text=LONG_TEXT)
MULTI_DOC = Fixture(name="multi-doc", kind="multi-doc", text=MULTI_DOC_TEXT)


ALL_FIXTURES: tuple[Fixture, ...] = (SHORT, LONG, MULTI_DOC)


def fixture_text_dir() -> str:
    """Filesystem directory that contains the on-disk .md mirrors of the fixtures.

    The runnable harness (``tools/parity_v1v2/run_harness.py``) reads the
    same fixtures from disk so the harness is reproducible from the
    repository alone.  Tests can also call this to assert the .md
    mirrors match the in-code constants.
    """
    import os

    # ``tools/parity_v1v2/fixtures`` — keep the source of truth on disk in
    # lockstep with the in-code constants via the README "Updating the
    # fixtures" section.
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "tools",
        "parity_v1v2",
        "fixtures",
    )


__all__ = [
    "Fixture",
    "SHORT",
    "LONG",
    "MULTI_DOC",
    "ALL_FIXTURES",
    "SHORT_TEXT",
    "LONG_TEXT",
    "MULTI_DOC_TEXT",
    "fixture_text_dir",
]