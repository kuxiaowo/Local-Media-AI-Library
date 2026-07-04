"""add scan stability indexes

Revision ID: 0014_scan_stability_indexes
Revises: 0013_conversational_search
Create Date: 2026-07-04
"""

from alembic import op


revision = "0014_scan_stability_indexes"
down_revision = "0013_conversational_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_jobs_type_status_created_at", "jobs", ["job_type", "status", "created_at"])
    op.create_index("ix_jobs_target_status", "jobs", ["target_id", "status"])
    op.create_index("ix_media_files_last_seen_at", "media_files", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_media_files_last_seen_at", table_name="media_files")
    op.drop_index("ix_jobs_target_status", table_name="jobs")
    op.drop_index("ix_jobs_type_status_created_at", table_name="jobs")
