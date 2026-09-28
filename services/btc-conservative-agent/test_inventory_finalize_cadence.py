"""Bounded cadence decisions; no production process or inventory is touched."""
import importlib.util
from pathlib import Path

import pytest


def worker():
    path = Path(__file__).with_name("data_sync_inventory_worker.py")
    spec = importlib.util.spec_from_file_location("cadence_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def receipt():
    return {"phase": "FINALIZE", "invocation_pages_written": 1,
            "invocation_elapsed_seconds": 0.005, "cpu_seconds": 0.004}


def test_short_advancing_finalize_yields_one_second():
    assert worker()._building_retry_seconds(receipt()) == 1


@pytest.mark.parametrize("field,value", [
    ("phase", "SCAN"), ("phase", "COMPLETE"),
    ("invocation_pages_written", 0), ("invocation_pages_written", -1),
    ("invocation_pages_written", True), ("invocation_pages_written", "1"),
    *[(key, value) for key in ("invocation_elapsed_seconds", "cpu_seconds")
      for value in (None, True, "0.005", -0.01, 0.101, float("nan"),
                    float("inf"), float("-inf"), 10**400, -(10**400))],
])
def test_unqualified_slice_keeps_five_second_yield(field, value):
    row = receipt()
    row[field] = value
    assert worker()._building_retry_seconds(row) == 5


@pytest.mark.parametrize("field", list(receipt()))
def test_missing_metric_keeps_five_second_yield(field):
    row = receipt()
    del row[field]
    assert worker()._building_retry_seconds(row) == 5


def test_one_tenth_second_boundary_is_inclusive():
    row = receipt()
    row.update(invocation_elapsed_seconds=0.1, cpu_seconds=0.1)
    assert worker()._building_retry_seconds(row) == 1
