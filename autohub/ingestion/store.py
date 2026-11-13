"""Content-addressed store for brochure PDFs.

A file is stored once under its SHA-256 and never overwritten, so every
``brochure_versions`` row keeps pointing at exactly the bytes that were
downloaded. The key returned by :meth:`BrochureStore.key_for` is what goes into
``brochure_versions.file_path``; it is relative to the store root so the data
directory can move (or be replaced by object storage) without rewriting rows.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class BrochureStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def key_for(sha256: str) -> str:
        """Storage key for a digest. Rejects anything that is not a lowercase SHA-256."""
        if not _SHA256_HEX.fullmatch(sha256):
            raise ValueError(f"not a SHA-256 hex digest: {sha256!r}")
        return f"{sha256}.pdf"

    def path_for(self, sha256: str) -> Path:
        return self.root / self.key_for(sha256)

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def new_temp_path(self) -> Path:
        """An empty file inside the store, so the later move is an atomic rename."""
        self.root.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=self.root, prefix="download-", suffix=".part")
        os.close(handle)
        return Path(name)

    def adopt(self, temp_path: Path, sha256: str) -> bool:
        """Move a finished download into place under its digest.

        The caller has already hashed ``temp_path``. Returns ``True`` if the
        file was added, ``False`` if that content was already stored (the
        existing file is kept and the temporary one removed).
        """
        target = self.path_for(sha256)
        if target.is_file():
            temp_path.unlink(missing_ok=True)
            return False
        os.replace(temp_path, target)
        return True
