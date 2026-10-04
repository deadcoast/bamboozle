"""Strict serialization and conservative publication primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 16 * 1024 * 1024


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Nonblocking process lock; the OS releases ownership on process exit."""
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OSError("Another Bamboozle view operation is active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class PublicationError(OSError):
    """The destination was published, but temporary-file cleanup failed."""

    published = True


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError(f"Non-finite JSON number: {value}")


def parse_json(data: str | bytes) -> Any:
    return json.loads(
        data, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant
    )


def json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def read_bytes(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    if limit < 0:
        raise ValueError("File limit must be nonnegative")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File exceeds the {limit}-byte limit: {path}")
    return data


def atomic_write(path: Path, data: bytes, *, overwrite: bool = False) -> None:
    """Publish complete bytes; default publication never replaces another file."""
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"Refusing to publish through a link: {path}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            # Windows rename refuses an existing destination, including on exFAT.
            os.rename(temporary, path)
        else:
            # POSIX rename replaces destinations; link provides no-clobber publication.
            os.link(temporary, path)
        published = True
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            if published:
                raise PublicationError(
                    f"Published {path}; temporary cleanup failed"
                ) from exc
            logger.warning("Could not clean up temporary file %s: %s", temporary, exc)
