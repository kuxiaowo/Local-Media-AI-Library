"""move embedding model to global settings

Revision ID: 0003_global_embedding_model
Revises: 0002_directory_prompt_context
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_global_embedding_model"
down_revision = "0002_directory_prompt_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("directory_rules", "embedding_model")


def downgrade() -> None:
    op.add_column(
        "directory_rules",
        sa.Column(
            "embedding_model",
            sa.Text(),
            nullable=False,
            server_default="nomic-embed-text",
        ),
    )
