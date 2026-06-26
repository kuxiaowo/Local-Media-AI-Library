"""add per-media background context

Revision ID: 0012_media_background_context
Revises: 0011_video_recursive_summary
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_media_background_context"
down_revision = "0011_video_recursive_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_files", sa.Column("background_context", sa.Text(), nullable=True))
    op.add_column("media_files", sa.Column("background_context_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_files", "background_context_prompt")
    op.drop_column("media_files", "background_context")
