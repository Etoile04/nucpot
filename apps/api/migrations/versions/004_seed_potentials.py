"""seed demonstration potentials

Revision ID: 004
Revises: 003
Create Date: 2026-06-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Seed the potentials table with demonstration records.

    Includes the 5 historical demonstration potentials from the legacy
    Supabase `000_init_potentials.sql` plus 3 records pointing at
    repo-local potential files (FeCrAl, U-MTP, W-Ta-V-Cr).
    """
    potentials = [
        {
            "name": "EAM_U_Zhou_2004",
            "type": "EAM",
            "elements": ["U"],
            "description": "EAM potential for U by Zhou (2004)",
            "lammps_config": {
                "pair_style": "eam/alloy",
                "pair_coeff": ["* * U.eam.alloy U"],
            },
            "source": "Zhou et al. 2004",
        },
        {
            "name": "EAM_Mo_Ackland1",
            "type": "EAM",
            "elements": ["Mo"],
            "description": "EAM potential for Mo (Ackland-Thompson)",
            "source": "Ackland & Thompson 2004",
        },
        {
            "name": "EAM_Zr_Mendelev_2007",
            "type": "EAM",
            "elements": ["Zr"],
            "description": "EAM potential for Zr (Mendelev-Ackland)",
            "source": "Mendelev & Ackland 2007",
        },
        {
            "name": "EAM_Nb_Mendelev_2012",
            "type": "EAM",
            "elements": ["Nb"],
            "description": "EAM potential for Nb",
            "source": "Mendelev et al. 2012",
        },
        {
            "name": "EAM_UMo_Xiang_2021",
            "type": "EAM",
            "elements": ["U", "Mo"],
            "description": "EAM potential for U-Mo alloy (Xiang et al.)",
            "source": "Xiang et al. 2021",
        },
        {
            "name": "EAM_FeCrAl_HNU",
            "type": "EAM",
            "elements": ["Fe", "Cr", "Al"],
            "description": "EAM potential for FeCrAl (HNU)",
            "file_url": "/uploads/FeCrAl_FS.eam.fs",
            "source": "HNU FeCrAl",
        },
        {
            "name": "MTP_U_MTP",
            "type": "MTP",
            "elements": ["U"],
            "description": "Machine-learning interatomic potential (MTP) for U",
            "file_url": "/uploads/U_MTP.mtp",
            "source": "U-MTP",
        },
        {
            "name": "EAM_WTaVCr_2023",
            "type": "EAM",
            "elements": ["W", "Ta", "V", "Cr"],
            "description": "EAM potential for W-Ta-V-Cr (2023)",
            "file_url": "/uploads/W-Ta-V-Cr_FS_2023.eam.fs",
            "source": "W-Ta-V-Cr 2023",
        },
    ]

    op.bulk_insert(
        sa.table(
            "potentials",
            sa.column("name", sa.String),
            sa.column("type", sa.String),
            sa.column("elements", sa.JSON),
            sa.column("description", sa.Text),
            sa.column("lammps_config", sa.JSON),
            sa.column("file_url", sa.String),
            sa.column("source", sa.String),
            sa.column("status", sa.String),
        ),
        [{**p, "status": "published"} for p in potentials],
    )


def downgrade() -> None:
    """Remove the seeded demonstration potentials."""
    seeded_names = (
        "EAM_U_Zhou_2004",
        "EAM_Mo_Ackland1",
        "EAM_Zr_Mendelev_2007",
        "EAM_Nb_Mendelev_2012",
        "EAM_UMo_Xiang_2021",
        "EAM_FeCrAl_HNU",
        "MTP_U_MTP",
        "EAM_WTaVCr_2023",
    )
    name_list = ", ".join(f"'{name}'" for name in seeded_names)
    op.execute(f"DELETE FROM potentials WHERE name IN ({name_list})")
