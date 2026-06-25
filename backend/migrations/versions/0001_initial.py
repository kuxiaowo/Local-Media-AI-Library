"""initial mysql schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

GUID = sa.CHAR(36)


def upgrade() -> None:
    op.create_table(
        "directory_rules",
        sa.Column("id", GUID, nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("normalized_path", sa.String(length=768), nullable=False),
        sa.Column("recursive", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("vision_model", sa.Text(), nullable=False),
        sa.Column("summary_model", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("video_frame_strategy", sa.String(length=32), nullable=False, server_default="hybrid"),
        sa.Column("frame_interval_seconds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("max_frames_per_video", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("analysis_detail", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_path"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )

    op.create_table(
        "media_files",
        sa.Column("id", GUID, nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("normalized_path", sa.String(length=768), nullable=False),
        sa.Column("root_path", sa.String(length=768), nullable=True),
        sa.Column("parent_dir", sa.Text(), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(length=128), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at_source", sa.String(length=64), nullable=True),
        sa.Column("captured_at_confidence", sa.String(length=16), nullable=True),
        sa.Column("file_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("folder_rule_id", GUID, nullable=True),
        sa.Column("resolved_config_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["folder_rule_id"], ["directory_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_path"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_media_files_root_path", "media_files", ["root_path"])
    op.create_index("ix_media_files_status", "media_files", ["status"])
    op.create_index("ix_media_files_captured_at", "media_files", ["captured_at"])

    op.create_table(
        "media_ai_summaries",
        sa.Column("media_id", GUID, nullable=False),
        sa.Column("model_used", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("short_summary", sa.Text(), nullable=True),
        sa.Column("detailed_summary", sa.Text(), nullable=True),
        sa.Column("scene", sa.Text(), nullable=True),
        sa.Column("objects", sa.JSON(), nullable=True),
        sa.Column("people", sa.JSON(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("text_visible", sa.JSON(), nullable=True),
        sa.Column("location_guess", sa.Text(), nullable=True),
        sa.Column("time_clues", sa.Text(), nullable=True),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column("search_keywords", sa.JSON(), nullable=True),
        sa.Column("searchable_text", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )

    op.create_table(
        "embedding_profiles",
        sa.Column("id", GUID, nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_name"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )

    op.create_table(
        "media_embeddings",
        sa.Column("id", GUID, nullable=False),
        sa.Column("media_id", GUID, nullable=False),
        sa.Column("profile_id", GUID, nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("embedded_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["embedding_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_id", "profile_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )

    op.create_table(
        "jobs",
        sa.Column("id", GUID, nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("target_id", GUID, nullable=True),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("media_embeddings")
    op.drop_table("embedding_profiles")
    op.drop_table("media_ai_summaries")
    op.drop_index("ix_media_files_captured_at", table_name="media_files")
    op.drop_index("ix_media_files_status", table_name="media_files")
    op.drop_index("ix_media_files_root_path", table_name="media_files")
    op.drop_table("media_files")
    op.drop_table("directory_rules")
