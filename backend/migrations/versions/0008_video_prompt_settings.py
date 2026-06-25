"""add configurable video prompts

Revision ID: 0008_video_prompt_settings
Revises: 0007_progressive_video_segments
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_video_prompt_settings"
down_revision = "0007_progressive_video_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("directory_rules", sa.Column("video_segment_prompt", sa.Text(), nullable=True))
    op.add_column("directory_rules", sa.Column("video_final_summary_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("directory_rules", "video_final_summary_prompt")
    op.drop_column("directory_rules", "video_segment_prompt")
