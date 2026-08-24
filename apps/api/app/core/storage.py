"""Content-addressed file storage for uploads.

Bytes live on the filesystem and only their metadata goes in Postgres (decision
log: image and document bytes outside the database). Files are addressed by the
SHA-256 of their content, which buys three things for free:

* **Deduplication.** The same rate sheet uploaded twice occupies one file. That
  happens constantly in practice — the corpus already contains
  ``KOBE TRAVEL AGENT RATES 2026-2027 PDF - Copy.pdf``.
* **Integrity.** The name *is* the checksum, so a corrupted or swapped file is
  detectable without a separate manifest.
* **Safety.** The stored name is derived from content, never from the
  user-supplied filename, so no upload can traverse out of the root or collide
  with another. The original name is kept in the database for display only.

Nothing here knows what a supplier document is; it stores bytes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

# The extension is cosmetic (it helps a human poking around the directory), so
# it is restricted to a safe set rather than trusted from the upload.
_SAFE_EXT = re.compile(r"^[a-z0-9]{1,8}$")


@dataclass(frozen=True)
class StoredFile:
    """Where a blob ended up, and what it is."""

    storage_path: str  # relative to the upload root, forward slashes
    checksum: str  # sha256 hex
    byte_size: int
    deduplicated: bool  # True when the identical content was already stored


def upload_root() -> Path:
    root = Path(settings.UPLOAD_ROOT)
    if not root.is_absolute():
        # app/core/storage.py -> app/core -> app -> the API app directory.
        root = Path(__file__).resolve().parents[2] / root
    return root


def _extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    return ext if _SAFE_EXT.match(ext) else "bin"


def save_bytes(content: bytes, *, filename: str, subdir: str) -> StoredFile:
    """Store ``content`` and return where it went.

    Writing is skipped when the identical content is already present, so this is
    idempotent: uploading the same document twice is cheap and reports itself.
    """
    checksum = hashlib.sha256(content).hexdigest()
    ext = _extension(filename)
    # A two-character fan-out directory keeps any one directory small enough for
    # a filesystem to stay quick once there are thousands of documents.
    rel = f"{subdir}/{checksum[:2]}/{checksum}.{ext}"
    target = upload_root() / rel

    if target.exists() and target.stat().st_size == len(content):
        return StoredFile(rel, checksum, len(content), deduplicated=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary name and move it into place, so a crash mid-write
    # cannot leave a truncated file sitting at a path that claims to be the
    # checksum of complete content.
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(content)
    tmp.replace(target)
    return StoredFile(rel, checksum, len(content), deduplicated=False)


def resolve(storage_path: str) -> Path:
    """Absolute path for a stored file, refusing anything outside the root.

    ``storage_path`` comes from our own database rather than from a request, but
    it is validated anyway: a path-traversal bug that only triggers after a bad
    row reaches the table is still a path-traversal bug.
    """
    root = upload_root().resolve()
    candidate = (root / storage_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"storage path escapes the upload root: {storage_path!r}")
    return candidate


def read_bytes(storage_path: str) -> bytes:
    return resolve(storage_path).read_bytes()
