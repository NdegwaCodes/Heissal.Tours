"""UUID v7 generation (time-ordered, index-friendly, safe to expose).

Python's stdlib has no uuid7 before 3.14, so we implement RFC 9562 §5.7 here.
Time-ordered UUIDs keep primary-key inserts append-friendly at scale.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a UUID version 7 (48-bit unix-ms timestamp + random)."""
    unix_ms = int(time.time() * 1000)
    ts_bytes = unix_ms.to_bytes(6, "big")
    rand = os.urandom(10)
    b = bytearray(ts_bytes + rand)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))
