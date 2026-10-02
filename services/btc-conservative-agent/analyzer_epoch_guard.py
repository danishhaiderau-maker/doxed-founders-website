"""Analyzer epoch guard: a current-cohort result never mixes data versions.

Loaders call :meth:`EpochGuard.admit` on every row they read (or
:meth:`EpochGuard.filter_frame` on a DataFrame); the guard keeps only rows of the
declared clean epoch (stamped ``data_epoch_id``, or unstampable/epoch-independent
rows timestamped at or after the epoch start) and counts what it rejected. The
generation receipt must carry :meth:`EpochGuard.receipt_block` under
``data_epoch``; the self-aware check ``data.compat_epoch_purity`` is RED when
that block is missing, names another epoch or reports
``pre_epoch_rows_admitted > 0``.

No epoch declared means no filtering (the block reports ``declared: false``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import data_epoch as de

ADMITTED_CLASSES = de.COMPATIBLE_CLASSES


class PreEpochRowError(RuntimeError):
    """A pre-epoch or foreign-epoch row reached a current-cohort result."""


class EpochGuard:
    def __init__(self, manifest: dict | None) -> None:
        self.manifest = manifest
        self.admitted: dict[str, int] = {}
        self.rejected: dict[str, dict[str, int]] = {}

    @classmethod
    def from_data_dir(cls, data_dir: str | os.PathLike) -> "EpochGuard":
        return cls(de.load_manifest(Path(data_dir) / de.MANIFEST_NAME))

    @property
    def declared(self) -> bool:
        return self.manifest is not None

    def admit(self, relpath: str, row: dict, ts_fields: Iterable[str] = de.TS_KEYS) -> bool:
        if not self.manifest:
            return True
        cls = de.classify_row(relpath, row, self.manifest, ts_fields)
        if cls in ADMITTED_CLASSES:
            self.admitted[relpath] = self.admitted.get(relpath, 0) + 1
            return True
        bucket = self.rejected.setdefault(relpath, {})
        bucket[cls] = bucket.get(cls, 0) + 1
        return False

    def filter_rows(self, relpath: str, rows: Iterable[dict], ts_fields: Iterable[str] = de.TS_KEYS) -> list[dict]:
        fields = tuple(ts_fields)
        return [row for row in rows if self.admit(relpath, row, fields)]

    def filter_frame(self, relpath: str, frame: Any, ts_fields: Iterable[str] = de.TS_KEYS):
        """:meth:`filter_rows` for a pandas DataFrame (returns a filtered copy)."""
        if not self.manifest or frame is None or len(frame) == 0:
            return frame
        fields = tuple(ts_fields)
        mask = [self.admit(relpath, row, fields) for row in frame.to_dict("records")]
        return frame[mask].copy()

    def assert_pure(self, relpath: str, rows: Iterable[dict], ts_fields: Iterable[str] = de.TS_KEYS) -> None:
        """Raise if any row of an already-built result is not clean-epoch."""
        if not self.manifest:
            return
        fields = tuple(ts_fields)
        for row in rows:
            cls = de.classify_row(relpath, row, self.manifest, fields)
            if cls not in ADMITTED_CLASSES:
                raise PreEpochRowError(f"{relpath}: {cls} row in a {self.manifest['epoch_id']} result")

    def receipt_block(self, *, pre_epoch_rows_admitted: int = 0) -> dict:
        rejected = sum(n for bucket in self.rejected.values() for n in bucket.values())
        manifest = self.manifest or {}
        return {"declared": self.declared, "epoch_id": manifest.get("epoch_id"),
                "started_at_utc": manifest.get("started_at_utc"),
                "rows_admitted": sum(self.admitted.values()), "pre_epoch_rows_rejected": rejected,
                "pre_epoch_rows_admitted": int(pre_epoch_rows_admitted),
                "rejected_by_stream": {k: dict(v) for k, v in sorted(self.rejected.items())}}
