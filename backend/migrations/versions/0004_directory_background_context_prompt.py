"""add directory background context prompt

Revision ID: 0004_bg_context_prompt
Revises: 0003_global_embedding_model
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_bg_context_prompt"
down_revision = "0003_global_embedding_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _has_column("directory_rules", "background_context_prompt"):
        op.add_column("directory_rules", sa.Column("background_context_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("directory_rules", "background_context_prompt"):
        op.drop_column("directory_rules", "background_context_prompt")


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))
