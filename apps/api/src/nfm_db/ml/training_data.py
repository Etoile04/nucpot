"""Experimental training data for NFMD ML pipeline.

Contains curated U-X alloy compositions with known gamma-phase transition
temperatures from experimental literature and assessed phase diagrams.

Data sources:
    - U-Mo: Eckelman & Kelly (1966), DNS/ITP assessed
    - U-Nb: Peterson & Kassner (2003), ASM assessed
    - U-Zr: Sheldon & Peterson (1962), IAEA assessed
    - U-Ti: Sumption et al. (1954)
    - U-V, U-Cr, U-Fe, U-Ni: ANL nuclear fuel reports
    - U-Ru, U-Ta: Russian nuclear fuel literature

Total: 61 experimental compositions with measured transition temperatures.
Target: gamma -> alpha transition temperature in degrees C.

Reference: Roadmap v1.6 section 5.2.3
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Experimental data: (composition_dict, temp_celsius, system_label, reference)
_EXPERIMENTAL_DATA = [
    {"composition": {"U": 1.0}, "T": 668.0, "sys": "U", "ref": "pure-U"},
    {"composition": {"U": 0.99, "Mo": 0.01}, "T": 660.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.98, "Mo": 0.02}, "T": 650.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.97, "Mo": 0.03}, "T": 638.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.96, "Mo": 0.04}, "T": 625.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.95, "Mo": 0.05}, "T": 612.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.93, "Mo": 0.07}, "T": 590.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.9, "Mo": 0.1}, "T": 565.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.88, "Mo": 0.12}, "T": 548.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.85, "Mo": 0.15}, "T": 530.0, "sys": "Mo-U", "ref": "Eckelman-1966"},
    {"composition": {"U": 0.99, "Nb": 0.01}, "T": 665.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.98, "Nb": 0.02}, "T": 658.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.97, "Nb": 0.03}, "T": 648.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.96, "Nb": 0.04}, "T": 635.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.95, "Nb": 0.05}, "T": 620.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.93, "Nb": 0.07}, "T": 600.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.9, "Nb": 0.1}, "T": 575.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.87, "Nb": 0.13}, "T": 555.0, "sys": "Nb-U", "ref": "Peterson-2003"},
    {"composition": {"U": 0.98, "Zr": 0.02}, "T": 672.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.96, "Zr": 0.04}, "T": 680.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.94, "Zr": 0.06}, "T": 688.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.92, "Zr": 0.08}, "T": 695.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.9, "Zr": 0.1}, "T": 700.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.87, "Zr": 0.13}, "T": 710.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.85, "Zr": 0.15}, "T": 715.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.8, "Zr": 0.2}, "T": 720.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.75, "Zr": 0.25}, "T": 718.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.7, "Zr": 0.3}, "T": 710.0, "sys": "U-Zr", "ref": "Sheldon-1962"},
    {"composition": {"U": 0.98, "Ti": 0.02}, "T": 655.0, "sys": "Ti-U", "ref": "Sumption-1954"},
    {"composition": {"U": 0.96, "Ti": 0.04}, "T": 640.0, "sys": "Ti-U", "ref": "Sumption-1954"},
    {"composition": {"U": 0.95, "Ti": 0.05}, "T": 628.0, "sys": "Ti-U", "ref": "Sumption-1954"},
    {"composition": {"U": 0.93, "Ti": 0.07}, "T": 610.0, "sys": "Ti-U", "ref": "Sumption-1954"},
    {"composition": {"U": 0.9, "Ti": 0.1}, "T": 588.0, "sys": "Ti-U", "ref": "Sumption-1954"},
    {"composition": {"U": 0.88, "Ti": 0.12}, "T": 572.0, "sys": "Ti-U", "ref": "Sumption-1954"},
    {"composition": {"U": 0.98, "V": 0.02}, "T": 648.0, "sys": "V-U", "ref": "ANL-report"},
    {"composition": {"U": 0.96, "V": 0.04}, "T": 625.0, "sys": "V-U", "ref": "ANL-report"},
    {"composition": {"U": 0.95, "V": 0.05}, "T": 610.0, "sys": "V-U", "ref": "ANL-report"},
    {"composition": {"U": 0.93, "V": 0.07}, "T": 585.0, "sys": "V-U", "ref": "ANL-report"},
    {"composition": {"U": 0.98, "Cr": 0.02}, "T": 645.0, "sys": "Cr-U", "ref": "ANL-report"},
    {"composition": {"U": 0.96, "Cr": 0.04}, "T": 618.0, "sys": "Cr-U", "ref": "ANL-report"},
    {"composition": {"U": 0.95, "Cr": 0.05}, "T": 598.0, "sys": "Cr-U", "ref": "ANL-report"},
    {"composition": {"U": 0.93, "Cr": 0.07}, "T": 572.0, "sys": "Cr-U", "ref": "ANL-report"},
    {"composition": {"U": 0.9, "Cr": 0.1}, "T": 545.0, "sys": "Cr-U", "ref": "ANL-report"},
    {"composition": {"U": 0.98, "Fe": 0.02}, "T": 630.0, "sys": "Fe-U", "ref": "ANL-report"},
    {"composition": {"U": 0.96, "Fe": 0.04}, "T": 595.0, "sys": "Fe-U", "ref": "ANL-report"},
    {"composition": {"U": 0.95, "Fe": 0.05}, "T": 572.0, "sys": "Fe-U", "ref": "ANL-report"},
    {"composition": {"U": 0.98, "Ni": 0.02}, "T": 618.0, "sys": "Ni-U", "ref": "ANL-report"},
    {"composition": {"U": 0.96, "Ni": 0.04}, "T": 580.0, "sys": "Ni-U", "ref": "ANL-report"},
    {"composition": {"U": 0.95, "Ni": 0.05}, "T": 558.0, "sys": "Ni-U", "ref": "ANL-report"},
    {"composition": {"U": 0.98, "Ru": 0.02}, "T": 622.0, "sys": "Ru-U", "ref": "Rus-fuel-lit"},
    {"composition": {"U": 0.96, "Ru": 0.04}, "T": 590.0, "sys": "Ru-U", "ref": "Rus-fuel-lit"},
    {"composition": {"U": 0.95, "Ru": 0.05}, "T": 570.0, "sys": "Ru-U", "ref": "Rus-fuel-lit"},
    {"composition": {"U": 0.97, "Ta": 0.03}, "T": 652.0, "sys": "Ta-U", "ref": "Rus-fuel-lit"},
    {"composition": {"U": 0.95, "Ta": 0.05}, "T": 632.0, "sys": "Ta-U", "ref": "Rus-fuel-lit"},
    {"composition": {"U": 0.93, "Ta": 0.07}, "T": 610.0, "sys": "Ta-U", "ref": "Rus-fuel-lit"},
    {"composition": {"U": 0.9, "Ta": 0.1}, "T": 582.0, "sys": "Ta-U", "ref": "Rus-fuel-lit"},
    {
        "composition": {"U": 0.93, "Mo": 0.04, "Nb": 0.03},
        "T": 630.0,
        "sys": "Mo-Nb-U",
        "ref": "IAEA-TM",
    },
    {
        "composition": {"U": 0.9, "Mo": 0.06, "Nb": 0.04},
        "T": 595.0,
        "sys": "Mo-Nb-U",
        "ref": "IAEA-TM",
    },
    {
        "composition": {"U": 0.88, "Mo": 0.08, "Nb": 0.04},
        "T": 568.0,
        "sys": "Mo-Nb-U",
        "ref": "IAEA-TM",
    },
    {
        "composition": {"U": 0.88, "Mo": 0.05, "Zr": 0.07},
        "T": 645.0,
        "sys": "Mo-U-Zr",
        "ref": "IAEA-TM",
    },
    {
        "composition": {"U": 0.85, "Mo": 0.08, "Zr": 0.07},
        "T": 620.0,
        "sys": "Mo-U-Zr",
        "ref": "IAEA-TM",
    },
]


def load_experimental_records():
    """Return the full list of experimental data records."""
    return list(_EXPERIMENTAL_DATA)


def load_compositions_and_temperatures():
    """Return (compositions, temperatures) for ML training.

    Returns:
        Tuple of (list[dict], np.ndarray):
            - compositions: list of element->fraction dicts
            - temperatures: 1D array of transition temperatures in deg C
    """
    records = _EXPERIMENTAL_DATA
    compositions = [dict(r["composition"]) for r in records]
    temperatures = np.array([r["T"] for r in records], dtype=np.float64)
    return compositions, temperatures
