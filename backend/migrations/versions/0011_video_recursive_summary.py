"""replace video segment events with recursive summaries

Revision ID: 0011_video_recursive_summary
Revises: 0010_video_segment_events
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_video_recursive_summary"
down_revision = "0010_video_segment_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "directory_rules",
        sa.Column("video_batch_overlap", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("directory_rules", "video_batch_overlap", server_default=None)
    op.add_column("video_segment_summaries", sa.Column("important_observations", sa.JSON(), nullable=True))
    op.add_column("video_segment_summaries", sa.Column("uncertain_points", sa.JSON(), nullable=True))
    op.drop_column("video_segment_summaries", "events")


def downgrade() -> None:
    op.add_column("video_segment_summaries", sa.Column("events", sa.JSON(), nullable=True))
    op.drop_column("video_segment_summaries", "uncertain_points")
    op.drop_column("video_segment_summaries", "important_observations")
    op.drop_column("directory_rules", "video_batch_overlap")
