"""add video frame summaries

Revision ID: 0005_video_frame_summaries
Revises: 0004_bg_context_prompt
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_video_frame_summaries"
down_revision = "0004_bg_context_prompt"
branch_labels = None
depends_on = None

GUID = sa.CHAR(36)


def upgrade() -> None:
    op.create_table(
        "video_frame_summaries",
        sa.Column("id", GUID, nullable=False),
        sa.Column("media_id", GUID, nullable=False),
        sa.Column("timestamp_seconds", sa.Float(), nullable=False),
        sa.Column("frame_path", sa.Text(), nullable=False),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("objects", sa.JSON(), nullable=True),
        sa.Column("people", sa.JSON(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("text_visible", sa.JSON(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_video_frame_summaries_media_id", "video_frame_summaries", ["media_id"])


def downgrade() -> None:
    op.drop_index("ix_video_frame_summaries_media_id", table_name="video_frame_summaries")
    op.drop_table("video_frame_summaries")
