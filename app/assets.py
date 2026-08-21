"""Fingerprints for the files a CDN will hold on to.

Cloudflare caches by full URL for a week, so a file that changes under the same name is
simply never fetched again. Every URL that can change carries `?v=<token>` instead, and
the token comes from the file itself.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def file_token(path: Path) -> str | None:
    """A short fingerprint of a file, from its size and mtime. None if it isn't a file.

    Size and mtime rather than the bytes: this runs for every GIF in the manifest each
    time the manifest is re-read, and a stat is free where hashing 47 MB is not.
    """
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except OSError:
        return None
    return hashlib.sha1(f"{stat.st_size}-{stat.st_mtime_ns}".encode()).hexdigest()[:10]
