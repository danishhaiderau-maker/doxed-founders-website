from unittest.mock import Mock

import pytest

from research_genome.bridge import GenomeBridge


def bridge():
    instance = object.__new__(GenomeBridge)
    instance.store = Mock()
    instance.store.reset.return_value = {"removed_bytes": 12}
    for field in ("_generation_identity", "_current_env_id", "_current_mkt_id", "_current_dec_id"):
        setattr(instance, field, "old")
    return instance


def test_default_retains_archive_reset_behavior():
    instance = bridge()
    instance.reset_research_store()
    instance.store.reset.assert_called_once_with()
    assert instance._generation_identity is None


def test_explicit_mode_forwards_real_preconditions():
    instance = bridge()
    states = {"ledger": "RECONCILED"}
    instance.reset_research_store(destructive=True, deletion_receipt_path="receipt.json",
                                  quiescent=True, recovery_states=states)
    instance.store.reset.assert_called_once_with(destructive=True,
        deletion_receipt_path="receipt.json", quiescent=True, recovery_states=states)


def test_failed_reset_does_not_clear_identity():
    instance = bridge()
    instance.store.reset.side_effect = ValueError("not quiescent")
    with pytest.raises(ValueError):
        instance.reset_research_store(destructive=True)
    assert instance._generation_identity == "old"


def test_truthy_string_cannot_enable_deletion():
    instance = bridge()
    with pytest.raises(ValueError):
        instance.reset_research_store(destructive="true")
    instance.store.reset.assert_not_called()
