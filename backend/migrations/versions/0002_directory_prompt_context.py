"""add directory prompt context

Revision ID: 0002_directory_prompt_context
Revises: 0001_initial
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_directory_prompt_context"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("directory_rules", sa.Column("custom_analysis_prompt", sa.Text(), nullable=True))
    op.add_column("directory_rules", sa.Column("background_context", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("directory_rules", "background_context")
    op.drop_column("directory_rules", "custom_analysis_prompt")
