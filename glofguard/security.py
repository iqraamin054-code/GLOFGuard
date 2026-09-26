"""Small, dependency-free helpers for keeping credentials out of persisted errors."""

from __future__ import annotations

import re
from collections.abc import Iterable


_OPAQUE_SUPABASE_KEY = re.compile(r"\bsb_(?:secret|publishable)_[A-Za-z0-9._-]+")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<!\w)(?P<prefix>(?P<key_quote>[\"']?)"
    r"(?:authorization|apikey|api[_-]?key|password|passwd|"
    r"(?:access[_-]?|refresh[_-]?|id[_-]?)?token|secret(?:[_-]?key)?|"
    r"supabase[_-](?:secret[_-]key|service[_-]role[_-]key))"
    r"(?P=key_quote)\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|(?:bearer\s+)?\[REDACTED\]|"
    r"(?:bearer\s+)?[^\s,;\]\}\"']+)"
)
# JWTs can appear without a header name, for example inside a provider error.
# A JSON object header encoded with base64url starts with `ey`; avoid matching
# ordinary dotted identifiers and versions while treating JWT-shaped text as secret.
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])ey[A-Za-z0-9_-]{6,}\."
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"
)
_URL_CREDENTIALS = re.compile(r"://[^/@\s]+@")


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match.group('prefix')}{quote}[REDACTED]{quote}"


def redact_sensitive_text(value: object, *, secrets: Iterable[str] = ()) -> str:
    """Return a diagnostic string with known credential shapes removed.

    This is deliberately used before errors enter SQLite or console-facing reports.
    It is not a logging transport and never attempts to inspect environment values.
    """

    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = _OPAQUE_SUPABASE_KEY.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _CREDENTIAL_ASSIGNMENT.sub(_redact_assignment, text)
    text = _URL_CREDENTIALS.sub("://[REDACTED]@", text)
    return text
