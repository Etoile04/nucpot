"""add_source_to_dft_calculations

Revision ID: 054b39a26310
Revises: f8e2db803b55
Create Date: 2026-07-21 08:58:13.851880

Idempotent fix (NFM-1692): If dft_calculations table does not exist (migration
023 was bypassed on a forked alembic branch), create the full table including
the source column.  Otherwise, just add the source column.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '054b39a26310'
down_revision: str | Sequence[str] | None = 'f8e2db803b55'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema — idempotent for dft_calculations table."""
    conn = op.get_bind()
    if conn.dialect.has_table(conn, 'dft_calculations'):
        op.add_column(
            'dft_calculations',
            sa.Column(
                'source',
                sa.String(100),
                nullable=True,
                comment='Data source tag (e.g. materials_project, incremental_200)',
            ),
        )
    else:
        _create_dft_calculations_table()


def _create_dft_calculations_table() -> None:
    """Create dft_calculations with full schema including source column.

    Mirrors migration 023's schema plus the source column from this migration.
    Used as fallback when 023 was bypassed on a forked alembic branch (NFM-1692).
    """
    op.create_table(
        'dft_calculations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'calculation_id',
            sa.String(length=200),
            nullable=False,
            comment='External calculation identifier (e.g. VASP job ID)',
        ),
        sa.Column(
            'material_id',
            sa.Uuid(),
            nullable=True,
            comment='FK to materials table; NULL if material not yet registered',
        ),
        sa.Column(
            'functional',
            sa.String(length=50),
            nullable=False,
            comment='XC functional (PBE, PBEsol, LDA, HSE06, etc.)',
        ),
        sa.Column(
            'cutoff_energy',
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            comment='Plane-wave cutoff energy in eV',
        ),
        sa.Column(
            'kpoint_mesh',
            sa.String(length=50),
            nullable=True,
            comment="K-point mesh string (e.g. '4x4x4', '8x8x8')",
        ),
        sa.Column(
            'kpoint_density',
            sa.Numeric(precision=10, scale=2),
            nullable=True,
            comment='K-point density in k-points/A^-3',
        ),
        sa.Column(
            'convergence_criteria',
            sa.String(length=200),
            nullable=True,
            comment='Energy convergence criterion (e.g. 1e-5 eV)',
        ),
        sa.Column(
            'exchange_correlation',
            sa.String(length=100),
            nullable=True,
            comment='Exchange-correlation detail beyond functional name',
        ),
        sa.Column(
            'pseudopotential',
            sa.String(length=200),
            nullable=True,
            comment='Pseudopotential library (PAW_PBE, USPP, etc.)',
        ),
        sa.Column(
            'spin_polarization',
            sa.JSON(),
            nullable=True,
            comment='Spin polarization settings (JSON: ispin, magmom, etc.)',
        ),
        sa.Column(
            'formation_energy',
            sa.Numeric(precision=16, scale=6),
            nullable=True,
            comment='Formation energy in eV/atom',
        ),
        sa.Column(
            'cohesive_energy',
            sa.Numeric(precision=16, scale=6),
            nullable=True,
            comment='Cohesive energy in eV/atom',
        ),
        sa.Column(
            'lattice_distortion',
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment='Lattice distortion parameter delta',
        ),
        sa.Column(
            'status',
            sa.String(length=50),
            nullable=True,
            comment='pending | running | completed | failed | cancelled',
        ),
        sa.Column(
            'computation_metadata',
            sa.JSON(),
            nullable=True,
            comment='Arbitrary computation metadata (VASP version, INCAR params, etc.)',
        ),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column(
            'source',
            sa.String(100),
            nullable=True,
            comment='Data source tag (e.g. materials_project, incremental_200)',
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['material_id'],
            ['materials.id'],
            name='fk_dft_calculations_material_id',
            ondelete='SET NULL',
        ),
        sa.UniqueConstraint(
            'calculation_id',
            name='uq_dft_calculations_calc_id',
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name='ck_dft_calculations_status',
        ),
    )
    op.create_index('idx_dft_calcs_material', 'dft_calculations', ['material_id'])
    op.create_index('idx_dft_calcs_calc_id', 'dft_calculations', ['calculation_id'])
    op.create_index('idx_dft_calcs_status', 'dft_calculations', ['status'])
    op.create_index('idx_dft_calcs_functional', 'dft_calculations', ['functional'])


def downgrade() -> None:
    """Downgrade schema — drop source column or entire table."""
    conn = op.get_bind()
    if not conn.dialect.has_table(conn, 'dft_calculations'):
        return
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('dft_calculations')]
    if 'source' in columns:
        op.drop_column('dft_calculations', 'source')
    else:
        # Table was created by this migration (023 was skipped) — drop it
        op.drop_index('idx_dft_calcs_functional', table_name='dft_calculations')
        op.drop_index('idx_dft_calcs_status', table_name='dft_calculations')
        op.drop_index('idx_dft_calcs_calc_id', table_name='dft_calculations')
        op.drop_index('idx_dft_calcs_material', table_name='dft_calculations')
        op.drop_table('dft_calculations')
