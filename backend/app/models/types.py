from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CHAR
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    """Store UUID values as CHAR(36), which works cleanly on MySQL."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(uuid.UUID(str(value)))

    def process_result_value(self, value: Any, dialect) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
