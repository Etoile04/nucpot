"""create MD verification tables for LAMMPS integration

Revision ID: 003b
Revises: 9c15710c6321
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision: str = '003b'
down_revision: Union[str, Sequence[str], None] = '9c15710c6321'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create MD verification tables for LAMMPS integration.

    Creates 5 new tables:
    - md_verification_jobs: Main job tracking
    - hpc_jobs: HPC cluster job execution details
    - md_simulation_results: MD simulation output data
    - defect_analysis_results: OVITO defect analysis results
    - potential_fitting_results: arc-dpa/RPA fitting results
    """
    # Create md_verification_jobs table
    op.create_table(
        'md_verification_jobs',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column('potential_id', sa.String(255), nullable=False),
        sa.Column('element_system', sa.String(100), nullable=False),
        sa.Column('phase', sa.String(100), nullable=True),
        sa.Column('config', JSONB, nullable=False),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'running', 'completed', 'failed')",
            name='check_md_job_status',
        ),
    )

    # Create hpc_jobs table
    op.create_table(
        'hpc_jobs',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'verification_job_id',
            UUID(as_uuid=True),
            sa.ForeignKey('md_verification_jobs.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('hpc_cluster', sa.String(100), nullable=False),
        sa.Column('hpc_job_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('partition', sa.String(100), nullable=True),
        sa.Column('nodes', sa.Integer(), nullable=True),
        sa.Column('walltime_requested', sa.Integer(), nullable=True),
        sa.Column('walltime_used', sa.Integer(), nullable=True),
        sa.Column('submission_output', sa.Text(), nullable=True),
        sa.Column('submission_errors', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
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
    )

    # Create md_simulation_results table
    op.create_table(
        'md_simulation_results',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'verification_job_id',
            UUID(as_uuid=True),
            sa.ForeignKey('md_verification_jobs.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('trajectory_file_path', sa.String(500), nullable=True),
        sa.Column('thermodynamic_data', JSONB, nullable=True),
        sa.Column('simulation_time_ps', sa.Float(), nullable=True),
        sa.Column('steps_completed', sa.Integer(), nullable=True),
        sa.Column('final_energy', sa.Float(), nullable=True),
        sa.Column('final_temperature', sa.Float(), nullable=True),
        sa.Column('final_pressure', sa.Float(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )

    # Create defect_analysis_results table
    op.create_table(
        'defect_analysis_results',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'verification_job_id',
            UUID(as_uuid=True),
            sa.ForeignKey('md_verification_jobs.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('defect_type', sa.String(100), nullable=False),
        sa.Column('concentration', sa.Float(), nullable=False),
        sa.Column('formation_energy', sa.Float(), nullable=True),
        sa.Column('metadata', JSONB, nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "defect_type IN ('vacancy', 'interstitial', 'dislocation', 'grain_boundary', 'other')",
            name='check_defect_type',
        ),
    )

    # Create potential_fitting_results table
    op.create_table(
        'potential_fitting_results',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'verification_job_id',
            UUID(as_uuid=True),
            sa.ForeignKey('md_verification_jobs.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('fitting_method', sa.String(50), nullable=False),
        sa.Column('parameters', JSONB, nullable=False),
        sa.Column('quality_metrics', JSONB, nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fitting_method IN ('arc-dpa', 'RPA', 'other')",
            name='check_fitting_method',
        ),
    )

    # Create indexes for performance
    op.create_index(
        'idx_md_jobs_status',
        'md_verification_jobs',
        ['status'],
        unique=False,
    )
    op.create_index(
        'idx_md_jobs_potential',
        'md_verification_jobs',
        ['potential_id'],
        unique=False,
    )
    op.create_index(
        'idx_hpc_jobs_verification',
        'hpc_jobs',
        ['verification_job_id'],
        unique=False,
    )
    op.create_index(
        'idx_hpc_jobs_cluster',
        'hpc_jobs',
        ['hpc_cluster'],
        unique=False,
    )
    op.create_index(
        'idx_defect_results_job',
        'defect_analysis_results',
        ['verification_job_id'],
        unique=False,
    )
    op.create_index(
        'idx_fitting_results_job',
        'potential_fitting_results',
        ['verification_job_id'],
        unique=False,
    )


def downgrade() -> None:
    """Drop MD verification tables and indexes."""
    # Drop indexes first
    op.drop_index('idx_fitting_results_job', table_name='potential_fitting_results')
    op.drop_index('idx_defect_results_job', table_name='defect_analysis_results')
    op.drop_index('idx_hpc_jobs_cluster', table_name='hpc_jobs')
    op.drop_index('idx_hpc_jobs_verification', table_name='hpc_jobs')
    op.drop_index('idx_md_jobs_potential', table_name='md_verification_jobs')
    op.drop_index('idx_md_jobs_status', table_name='md_verification_jobs')

    # Drop tables in reverse order of creation (to handle FK dependencies)
    op.drop_table('potential_fitting_results')
    op.drop_table('defect_analysis_results')
    op.drop_table('md_simulation_results')
    op.drop_table('hpc_jobs')
    op.drop_table('md_verification_jobs')
