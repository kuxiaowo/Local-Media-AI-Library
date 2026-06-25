"""add video frame max width

Revision ID: 0006_video_frame_max_width
Revises: 0005_video_frame_summaries
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_video_frame_max_width"
down_revision = "0005_video_frame_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "directory_rules",
        sa.Column("video_frame_max_width", sa.Integer(), nullable=False, server_default="1280"),
    )
    op.alter_column("directory_rules", "video_frame_max_width", server_default=None)


def downgrade() -> None:
    op.drop_column("directory_rules", "video_frame_max_width")
