import os
import re
import tempfile
from pathlib import Path
from kdaa.errors import KDAAError

# Source bytes and normalized text, keyed by workspace and document.
SOURCE_KEY = r"[0-9a-f-]{36}/[0-9a-f-]{36}\.(?:raw|txt)"
# Recorded model request/response bodies, keyed by run and call number. These stay on the asset
# volume so prompts and model output never reach Git, the database or ordinary logs.
LLM_KEY = r"llm/[0-9a-f-]{36}/[0-9]{2}-[a-z]{1,20}\.json"

class LocalAssetStore:
    """Immutable generated keys, atomic per-file writes; not a DB/filesystem transaction."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
    def _path(self, key: str) -> Path:
        if not re.fullmatch(f"(?:{SOURCE_KEY}|{LLM_KEY})", key):
            raise KDAAError("invalid_storage_key", "Invalid storage key.")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise KDAAError("invalid_storage_key", "Storage key escapes its root.")
        return path
    def put(self, key: str, content: bytes) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise KDAAError("immutable_source", "Stored sources cannot be overwritten.", 409)
        fd, temp = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Same-filesystem hard link atomically publishes the fully written file.
            # Unlike replace(), it fails rather than overwriting an existing key.
            os.link(temp, target)
        except Exception:
            # A failed write cannot be admitted as a document by the service.
            raise
        finally:
            Path(temp).unlink(missing_ok=True)
    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise KDAAError("source_missing", "A stored source is missing; restore the asset volume.", 409) from exc
    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
    def ready(self) -> bool:
        try:
            fd, path = tempfile.mkstemp(prefix=".ready-", dir=self.root)
            os.close(fd)
            Path(path).unlink()
            return True
        except OSError:
            return False
