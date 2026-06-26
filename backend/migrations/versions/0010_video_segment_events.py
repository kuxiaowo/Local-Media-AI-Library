"""add video segment events

Revision ID: 0010_video_segment_events
Revises: 0009_video_system_prompts
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_video_segment_events"
down_revision = "0009_video_system_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_segment_summaries", sa.Column("events", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_segment_summaries", "events")
