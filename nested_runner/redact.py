from __future__ import annotations

import re

_SESSION_PATTERN = re.compile(r"(sessionId=)[0-9a-f-]+")
_OPAQUE_PATTERN = re.compile(r"(githubusercontent\.com/)[^/]+")


def redact(text: str) -> str:
    return _OPAQUE_PATTERN.sub(r"\1<opaque>", _SESSION_PATTERN.sub(r"\1<session>", text))
