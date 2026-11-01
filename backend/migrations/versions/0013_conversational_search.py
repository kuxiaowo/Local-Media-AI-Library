"""add conversational search sessions

Revision ID: 0013_conversational_search
Revises: 0012_media_background_context
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa

from app.models.types import GUID


revision = "0013_conversational_search"
down_revision = "0012_media_background_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_conversations",
        sa.Column("id", GUID, nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_search_conversations_last_message_at",
        "search_conversations",
        ["last_message_at"],
    )

    op.create_table(
        "search_messages",
        sa.Column("id", GUID, nullable=False),
        sa.Column("conversation_id", GUID, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=True),
        sa.Column("tool_events", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["search_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_search_messages_conversation_created_at",
        "search_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_messages_conversation_created_at", table_name="search_messages")
    op.drop_table("search_messages")
    op.drop_index("ix_search_conversations_last_message_at", table_name="search_conversations")
    op.drop_table("search_conversations")
