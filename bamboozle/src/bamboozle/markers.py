"""Versioned, nonrecursive quoting of literal cipher-prefix syntax.

The escape alternative lives inside the already reserved CASE namespace. Its
non-hex `x_` discriminator cannot be a legacy case mask or a dictionary term.
Only an exact reserved prefix can be carried, never arbitrary encoded payloads.
"""

from __future__ import annotations

import re

from bamboozle.dictionary import CASE_PREFIX, LITERAL_PREFIX
from bamboozle.utils import MAX_FILE_BYTES

LEGACY = 1
CURRENT = 2
PREFIX = re.compile(
    re.escape(LITERAL_PREFIX) + "|" + re.escape(CASE_PREFIX), re.IGNORECASE
)
ESCAPE_PREFIX = CASE_PREFIX + "x_"
ESCAPE = re.compile(
    re.escape(ESCAPE_PREFIX) + r"(?P<escape_hex>[0-9a-f]{20,48})__", re.IGNORECASE
)


def validate_version(value: object) -> int:
    if type(value) is not int or value not in (LEGACY, CURRENT):
        raise ValueError("Unsupported marker version; expected integer 1 or 2")
    return value


def quote_prefix(prefix: str) -> str:
    """Quote exactly one literal prefix, retaining its original spelling/case."""
    if PREFIX.fullmatch(prefix) is None:
        raise ValueError("Escape payload must be an exact reserved marker prefix")
    return ESCAPE_PREFIX + prefix.encode("utf-8").hex() + "__"


def unquote_prefix(token: str) -> str:
    """Validate canonical syntax and restricted payload before decoding once."""
    match = ESCAPE.fullmatch(token)
    if match is None:
        raise ValueError("Damaged or unrecognized prefix escape")
    try:
        prefix = bytes.fromhex(match.group("escape_hex")).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Prefix escape payload is not valid canonical UTF-8") from exc
    if quote_prefix(prefix) != token:
        raise ValueError("Prefix escape must retain its canonical spelling")
    return prefix


def quote_prefixes(text: str, *, limit: int = MAX_FILE_BYTES) -> str:
    """Quote raw marker examples in a newly authored snippet, without a dictionary.

    This treats every prefix as original text. Do not apply it to an already
    encoded document or markers that are meant to operate as literal/case tokens.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError("Quoting byte limit must be positive")
    size = len(text.encode("utf-8"))
    if size > limit:
        raise ValueError("Quoted marker text exceeds its byte limit")

    def replace(match: re.Match[str]) -> str:
        nonlocal size
        result = quote_prefix(match.group())
        size += len(result) - len(match.group().encode("utf-8"))
        if size > limit:
            raise ValueError("Quoted marker text exceeds its byte limit")
        return result

    return PREFIX.sub(replace, text)
