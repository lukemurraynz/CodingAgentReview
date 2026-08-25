"""Finding deduplication keys (FR-015).

V1 strategy: file-path + normalized content hash — deterministic and cheap.
The public API (``make_key``) is the swap point for symbol-level or AST-based
matching later; callers must not depend on key internals.
"""

import hashlib

_KEY_VERSION = "v1"


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().lower()


def _normalize_content(content: str) -> str:
    lines = [line.rstrip() for line in content.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def make_key(file_path: str, content: str) -> str:
    """Stable dedup key for a finding anchored to one file's normalized content."""
    normalized = f"{_normalize_path(file_path)}\n\x1f\n{_normalize_content(content)}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{_KEY_VERSION}:{digest}"
