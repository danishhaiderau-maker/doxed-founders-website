"""Load the canonical lifecycle cleanup transaction implementation.

The signal engine mirrors bot.py byte-for-byte while the implementation remains
owned by the conservative-agent service.  Fail closed if that source is absent.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "btc-conservative-agent" / "lifecycle_cleanup_transaction.py"
_SPEC = spec_from_file_location("_canonical_lifecycle_cleanup_transaction", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("canonical lifecycle cleanup transaction is unavailable")
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

CleanupRejected = _MODULE.CleanupRejected
CleanupTransaction = _MODULE.CleanupTransaction
verify_bundle = _MODULE.verify_bundle
