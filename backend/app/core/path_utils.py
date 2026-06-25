from __future__ import annotations

import os
import re
from pathlib import Path


def normalize_path(path: str | os.PathLike[str], *, strict: bool = False) -> str:
    raw = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
    try:
        resolved = Path(raw).resolve(strict=strict)
        normalized = str(resolved)
    except OSError:
        normalized = os.path.abspath(raw)

    normalized = normalized.replace("\\", "/").rstrip("/")

    if os.name == "nt" or re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("//"):
        normalized = normalized.lower()

    return normalized


def path_has_prefix(path: str, prefix: str) -> bool:
    normalized = normalize_path(path)
    normalized_prefix = normalize_path(prefix)
    return normalized == normalized_prefix or normalized.startswith(normalized_prefix + "/")


def path_is_within(path: str, roots: list[str]) -> bool:
    return any(path_has_prefix(path, root) for root in roots)


def parent_dir(path: str) -> str:
    return normalize_path(Path(path).parent)
