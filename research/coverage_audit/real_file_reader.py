"""Pinned-root reader for explicit relative paths. No discovery."""

from __future__ import annotations

import os
import stat

STORED_CAP = 64 * 1024
REJECT = "rejected"


class PinError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _parts(rel: str) -> list[str]:
    if not isinstance(rel, str) or rel == "" or rel.startswith("/") or "\\" in rel:
        raise PinError(REJECT)
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PinError(REJECT)
    return parts


class PinnedRoot:
    def __init__(self, root: str):
        if not isinstance(root, str) or root == "":
            raise PinError(REJECT)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        else:
            raise PinError("unsupported")
        try:
            self._fd = os.open(root, flags)
        except OSError as exc:
            raise PinError(REJECT) from exc
        info = os.fstat(self._fd)
        self.identity = (info.st_dev, info.st_ino)

    def open_read(self, rel: str, *, follow: bool = False) -> "PinnedFile":
        parts = _parts(rel)
        directory = self._fd
        opened: list[int] = []
        try:
            for part in parts[:-1]:
                if _linked(directory, part):
                    raise PinError(REJECT)
                directory = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory,
                )
                opened.append(directory)
            name = parts[-1]
            if _linked(directory, name):
                raise PinError(REJECT)
            flags = os.O_RDONLY | os.O_CLOEXEC
            if not follow:
                flags |= os.O_NOFOLLOW
            handle = os.open(name, flags, dir_fd=directory)
        except PinError:
            _close_all(opened)
            raise
        except OSError as exc:
            _close_all(opened)
            raise PinError(REJECT) from exc
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_size > STORED_CAP:
            os.close(handle)
            _close_all(opened)
            raise PinError(REJECT)
        _close_all(opened)
        return PinnedFile(handle, (info.st_dev, info.st_ino), info.st_size)


def _linked(directory: int, name: str) -> bool:
    try:
        os.readlink(name, dir_fd=directory)
    except OSError:
        return False
    return True


def _close_all(handles: list[int]) -> None:
    for handle in handles:
        try:
            os.close(handle)
        except OSError:
            pass


class PinnedFile:
    def __init__(self, handle: int, identity: tuple[int, int], size: int):
        self._handle = handle
        self._identity = identity
        self._size = size

    def size(self) -> int:
        info = os.fstat(self._handle)
        if (info.st_dev, info.st_ino) != self._identity or info.st_size != self._size:
            raise PinError(REJECT)
        return self._size

    def identity(self) -> tuple[int, int]:
        return self._identity

    def read_chunks(self, count: int):
        os.lseek(self._handle, 0, os.SEEK_SET)
        left = self._size
        while left:
            chunk = os.read(self._handle, min(count, left))
            if not chunk:
                break
            left -= len(chunk)
            if self.identity() != self._identity:
                raise PinError(REJECT)
            yield chunk

    def readback(self) -> bytes:
        os.lseek(self._handle, 0, os.SEEK_SET)
        data = os.read(self._handle, self._size + 1)
        info = os.fstat(self._handle)
        if (info.st_dev, info.st_ino) != self._identity or len(data) != self._size:
            raise PinError(REJECT)
        return data
