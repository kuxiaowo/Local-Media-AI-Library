"""add progressive video segments

Revision ID: 0007_progressive_video_segments
Revises: 0006_video_frame_max_width
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_progressive_video_segments"
down_revision = "0006_video_frame_max_width"
branch_labels = None
depends_on = None

GUID = sa.CHAR(36)


def upgrade() -> None:
    op.add_column(
        "directory_rules",
        sa.Column("video_batch_size", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column("directory_rules", sa.Column("video_frame_max_height", sa.Integer(), nullable=True))
    op.alter_column("directory_rules", "video_batch_size", server_default=None)

    op.create_table(
        "video_segment_summaries",
        sa.Column("id", GUID, nullable=False),
        sa.Column("media_id", GUID, nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("start_time_seconds", sa.Float(), nullable=True),
        sa.Column("end_time_seconds", sa.Float(), nullable=True),
        sa.Column("frame_paths", sa.JSON(), nullable=True),
        sa.Column("current_segment_summary", sa.Text(), nullable=True),
        sa.Column("current_segment_tags", sa.JSON(), nullable=True),
        sa.Column("important_objects", sa.JSON(), nullable=True),
        sa.Column("ocr_text", sa.JSON(), nullable=True),
        sa.Column("new_objects_or_scenes", sa.JSON(), nullable=True),
        sa.Column("updated_global_summary", sa.Text(), nullable=True),
        sa.Column("updated_timeline", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_video_segment_summaries_media_id", "video_segment_summaries", ["media_id"])

    op.add_column("video_frame_summaries", sa.Column("segment_id", GUID, nullable=True))
    op.add_column("video_frame_summaries", sa.Column("frame_index", sa.Integer(), nullable=True))
    op.create_index("ix_video_frame_summaries_segment_id", "video_frame_summaries", ["segment_id"])
    op.create_foreign_key(
        "fk_video_frame_summaries_segment_id",
        "video_frame_summaries",
        "video_segment_summaries",
        ["segment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_video_frame_summaries_segment_id", "video_frame_summaries", type_="foreignkey")
    op.drop_index("ix_video_frame_summaries_segment_id", table_name="video_frame_summaries")
    op.drop_column("video_frame_summaries", "frame_index")
    op.drop_column("video_frame_summaries", "segment_id")

    op.drop_index("ix_video_segment_summaries_media_id", table_name="video_segment_summaries")
    op.drop_table("video_segment_summaries")

    op.drop_column("directory_rules", "video_frame_max_height")
    op.drop_column("directory_rules", "video_batch_size")
