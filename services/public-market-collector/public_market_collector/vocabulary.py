"""Record semantics boundary, independent of the existing structural schemas.

Absent record_vocab_v means PR988's closed pmc_vocab_v1 series. New writers emit
pmc_vocab_v2 on EVERY top-level record. Do not concatenate series in an analyzer
before calling pool_records (normalization also calls it). Byte/checksum readers
remain forensic primitives, not permission to pool or resume a legacy series.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Iterable

RECORD_VOCAB_FIELD = "record_vocab_v"
LEGACY_VOCAB = "pmc_vocab_v1"
CURRENT_VOCAB = "pmc_vocab_v2"


class VocabularyError(ValueError):
    """Unknown, mixed, or unsupported semantics; never silently relabel them."""


def record_vocab_version(record: dict) -> str:
    if not isinstance(record, dict):
        raise VocabularyError("RECORD_NOT_OBJECT")
    # Only absence implies legacy; null/empty/unknown explicit values refuse.
    version = record.get(RECORD_VOCAB_FIELD, LEGACY_VOCAB)
    if not isinstance(version, str) or version not in (LEGACY_VOCAB, CURRENT_VOCAB):
        raise VocabularyError("UNKNOWN_RECORD_VOCAB")
    return version


def stamp_current(record: dict) -> dict:
    """For NEW writer-owned records, not a migration API. Refuses explicit v1."""
    if RECORD_VOCAB_FIELD in record and record_vocab_version(record) != CURRENT_VOCAB:
        raise VocabularyError("WRITER_CANNOT_RELABEL_LEGACY")
    return {**record, RECORD_VOCAB_FIELD: CURRENT_VOCAB}


def require_current_series(records: Iterable[dict]) -> None:
    """Writer-side resume barrier: PR988 captures are separate CLOSED series."""
    for record in records:
        if record_vocab_version(record) != CURRENT_VOCAB:
            raise VocabularyError("VOCAB_CLOSED_SERIES_NEW_ROOT_REQUIRED")


@dataclass(frozen=True)
class ReviewedVocabMapping:
    """Explicit reader-only v1 -> v2 semantic conversion.

    HM must review the actual converter/meaning and pin the review receipt before
    use. The API checks its supplied SHA256 reference, not HM's identity or the
    truth of that review; this is NOT an authorization service. No production
    mapping is shipped. A boolean 'allow_mixed' or identity fallback is absent.
    """
    hm_review_sha256: str
    legacy_to_current: Callable[[dict], dict]

    def __post_init__(self):
        if (not isinstance(self.hm_review_sha256, str)
                or len(self.hm_review_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.hm_review_sha256)
                or not callable(self.legacy_to_current)):
            raise VocabularyError("EXPLICIT_HM_REVIEWED_MAPPING_REQUIRED")


def pool_records(records: Iterable[dict], *, reviewed_mapping: ReviewedVocabMapping | None = None) -> list[dict]:
    """Refuse cross-vocabulary pooling by default; preserve input order/objects.

    Homogeneous legacy records remain legacy and may be read as a closed series.
    An explicitly supplied reviewed converter must produce CURRENT_VOCAB, not
    just assert compatibility. Unknown versions always refuse. Conversion copies
    the input deeply, removes stale journal checksums and records provenance.
    Writers still refuse to append to an existing legacy series, mapping or not.
    """
    rows = list(records)
    versions = {record_vocab_version(row) for row in rows}
    if reviewed_mapping is not None and not isinstance(reviewed_mapping, ReviewedVocabMapping):
        raise VocabularyError("EXPLICIT_HM_REVIEWED_MAPPING_REQUIRED")
    if len(versions) > 1 and reviewed_mapping is None:
        raise VocabularyError("MIXED_RECORD_VOCAB_HM_MAPPING_REQUIRED")
    output = []
    for row in rows:
        version = record_vocab_version(row)
        if version == LEGACY_VOCAB and reviewed_mapping is not None:
            mapped = reviewed_mapping.legacy_to_current(deepcopy(row))
            if record_vocab_version(mapped) != CURRENT_VOCAB:
                raise VocabularyError("MAPPING_DID_NOT_PRODUCE_CURRENT_VOCAB")
            mapped = dict(mapped)
            mapped.pop("record_checksum", None)  # checksum no longer describes mapped bytes
            mapped["source_record_vocab_v"] = LEGACY_VOCAB
            mapped["vocab_mapping_hm_review_sha256"] = reviewed_mapping.hm_review_sha256
            output.append(mapped)
        else:
            output.append(dict(row))
    return output
