"""Secret redaction before persistence or projection (FR-020).

Patterns are explicit and typed so findings/logs never echo secret values.
"""

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    (
        "azure_ad_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
    ),
    ("account_key", re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}", re.IGNORECASE)),
    (
        "bearer",
        re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{20,}={0,2}"),
    ),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "generic_secret_kv",
        re.compile(r"(?i)\b(secret|password|passwd|api_?key|client_secret)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{8,}"),
    ),
]

_REPLACEMENT = "[REDACTED:{kind}]"


def redact_text(text: str) -> tuple[str, int]:
    """Return (redacted text, number of replacements made)."""
    count = 0
    out = text
    for kind, pattern in _PATTERNS:
        out, n = pattern.subn(_REPLACEMENT.format(kind=kind), out)
        count += n
    return out, count


def redact_finding_fields(*texts: str) -> list[str]:
    """Redact each text; used for title/detail/evidence notes pre-persistence."""
    return [redact_text(t)[0] for t in texts]
