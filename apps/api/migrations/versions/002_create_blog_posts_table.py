"""create blog posts table with workflow metadata

Revision ID: 002
Revises: 001
Create Date: 2025-06-13 13:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, Sequence[str], None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create blog_posts table with workflow metadata."""
    # Create blog_posts table
    op.create_table(
        'blog_posts',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column('slug', sa.String(255), nullable=False),
        sa.Column(
            'status',
            sa.String(20),
            nullable=False,
            server_default='draft',
        ),
        sa.Column(
            'author_id',
            UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'reviewer_id',
            UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'reviewed_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            'published_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            'rejection_reason',
            sa.Text(),
            nullable=True,
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
        sa.CheckConstraint(
            "status IN ('draft', 'under_review', 'approved', 'published', 'rejected')",
            name='check_post_status',
        ),
    )

    # Create indexes for efficient querying
    op.create_index(op.f('ix_blog_posts_slug'), 'blog_posts', ['slug'], unique=True)
    op.create_index(op.f('ix_blog_posts_status'), 'blog_posts', ['status'], unique=False)
    op.create_index(op.f('ix_blog_posts_author_id'), 'blog_posts', ['author_id'], unique=False)
    op.create_index(op.f('ix_blog_posts_reviewer_id'), 'blog_posts', ['reviewer_id'], unique=False)


def downgrade() -> None:
    """Drop blog_posts table and indexes."""
    op.drop_index(op.f('ix_blog_posts_reviewer_id'), table_name='blog_posts')
    op.drop_index(op.f('ix_blog_posts_author_id'), table_name='blog_posts')
    op.drop_index(op.f('ix_blog_posts_status'), table_name='blog_posts')
    op.drop_index(op.f('ix_blog_posts_slug'), table_name='blog_posts')
    op.drop_table('blog_posts')
