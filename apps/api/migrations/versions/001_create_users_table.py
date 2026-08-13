"""create users table with blog role

Revision ID: 001
Revises:
Create Date: 2025-06-13 12:48:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create users table with blog role support."""
    # CREATE TYPE does not support IF NOT EXISTS in PostgreSQL (any version).
    # Use DO block with pg_type check for idempotent enum creation (PG 14/15/16).
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'blog_role_enum') THEN "
        "CREATE TYPE blog_role_enum AS ENUM ('admin', 'editor', 'reviewer'); "
        "END IF; "
        "END $$;"
    )


    op.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username VARCHAR(100) NOT NULL,
            email VARCHAR(255) NOT NULL,
            full_name VARCHAR(255),
            hashed_password VARCHAR(255) NOT NULL,
            blog_role blog_role_enum,
            is_active BOOLEAN NOT NULL DEFAULT true,
            last_login TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT check_blog_role CHECK (blog_role IN ('admin', 'editor', 'reviewer'))
        )
    """)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)


def downgrade() -> None:
    """Drop users table and blog_role enum."""
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')
    op.execute("DROP TYPE IF EXISTS blog_role_enum")
