"""Storage boundary: server-selected opaque keys only. Author: Fubuki."""
from pathlib import Path
from typing import Protocol
import hashlib
import os
import re


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> tuple[int, str]: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise ValueError("Invalid storage key")
        path = self.root / key
        if path.is_symlink():
            raise ValueError("Symbolic links are not artifacts")
        return path

    def put(self, key, data):
        path = self.path(key)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        return len(data), hashlib.sha256(data).hexdigest()

    def get(self, key):
        return self.path(key).read_bytes()

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)


class S3Storage:
    """Extension point; deliberately unavailable until scoped adapter is implemented."""
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("S3 is reserved for V1; use local storage")
