# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Reading and writing the module's JSON files, and the two guarantees they share.

Both the detection file and the review file are read as UTF-8 and written through a
temporary file that is renamed into place. The encoding matters because JSON is UTF-8 by
definition while ``open()`` follows the platform locale; the rename matters because a review
is the record of a clinical decision, and a write that fails part-way through must leave the
previous file intact rather than a truncated one.
"""

import contextlib
import hashlib
import json
from pathlib import Path

#: Read size for hashing. Detection files are small; volumes are never hashed here.
_BLOCK = 1 << 16


def read_json(path):
    """Return the parsed contents of ``path``.

    Raises ``OSError`` if the file cannot be read and ``json.JSONDecodeError`` (a
    ``ValueError``) if it does not parse.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    """Write ``data`` to ``path`` as indented UTF-8 JSON, atomically.

    The temporary file is removed if anything fails, including a value that cannot be
    serialised — which is discovered part-way through ``json.dump``, after the file has
    already been truncated.

    Non-ASCII text is written literally rather than ``\\uXXXX``-escaped, so a reviewer's note
    is legible to anyone who opens the file — which is the point of an auditable record. That
    is what makes the explicit encoding matter on the way out as well as the way in.
    """
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temporary.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def sha256(path):
    """Return the hex SHA-256 of ``path``.

    The review records the digest of the detection file it was made against, so a detection
    file that is regenerated under a review can be spotted instead of silently changing what
    the reviewer is held to have seen.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()
