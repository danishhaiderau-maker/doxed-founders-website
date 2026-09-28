import hashlib
import json
import os
from pathlib import Path

import pytest

from research_reset_inventory import PROOF_SCHEMA, plan_research_reset


def proof(root):
    return {"schema": PROOF_SCHEMA, "runtime_root": str(root), "retired_epoch_id": "epoch-old",
            "new_epoch_id": "epoch-new", "source_revision": "a" * 40,
            "recovery_receipt_sha256": "b" * 64, "writers_quiesced": True,
            "paper_only": True, "live_disarmed": True, "epoch_retired": True,
            "pending_paper_orders": 0, "open_paper_positions": 0,
            "pending_wal_records": 0, "pending_recovery_records": 0}


def put(root, path, data=b"record\n"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_actual_accounting_retained_at_research_reset(tmp_path):
    for name in ("trades_3factor.csv", "expired_orders_3factor.csv"):
        put(tmp_path, name)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert result["targets"] == []
    assert len(result["retained"]) == 2
    assert all(row["reason"] == "ESSENTIAL_ORDER_PAPER_OR_ACCOUNTING_STATE"
               for row in result["retained"])


def test_quiescent_lifecycle_derived_index_is_retired_without_mutation(tmp_path):
    name = "v3/lifecycle_bundle_index/lifecycle_index.sqlite3"
    target = put(tmp_path, name)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert [r["path"] for r in result["targets"]] == [name]
    assert target.read_bytes() == b"record\n"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_lifecycle_sqlite_sidecars_require_coordinated_owner_reset(tmp_path, suffix):
    name = "v3/lifecycle_bundle_index/lifecycle_index.sqlite3"
    put(tmp_path, name)
    put(tmp_path, name + suffix)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert result["targets"] == []
    base = next(r for r in result["retained"] if r["path"] == name)
    assert base["reason"] == "LIFECYCLE_SQLITE_SIDECARS_REQUIRE_OWNER_RESET"


def test_exact_allowlist_and_no_mutation(tmp_path):
    names = ["signal_replay.jsonl", "signal_replay.jsonl.2", "v3/ledgers/opportunity.jsonl",
             "v3/ledgers/lifecycle.jsonl.3", "v3/market_segments/aa/" + "a" * 64 + ".json"]
    for name in names:
        put(tmp_path, name)
    for name in ["v3/mystery.json", "v3/ledgers/new_unknown.jsonl", "signal_replay.jsonl.secret",
                 "v3/market_segments/bb/" + "a" * 64 + ".json"]:
        put(tmp_path, name)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert result["complete"] and result["read_only"]
    assert {r["path"] for r in result["targets"]} == set(names)
    assert len(result["retained"]) == 4
    assert before == {p: p.read_bytes() for p in before}
    assert result == plan_research_reset(tmp_path, proof=proof(tmp_path))


@pytest.mark.parametrize("name", ["open_positions.json", "paper_lifecycle_v1.json", ".env",
    "v3/.locks/opportunity.lock", "v3/emergency_evidence_wal_v2/mandatory-reserve.bin",
    "v3/receipts/emergency_record_idempotency_v1/append_heads/lifecycle.json",
    "v3/receipts/ledger_generations_v1/lifecycle/ACTIVE.json", "research_events_v22.index.sqlite3-wal",
    "relay_lifecycle_evidence_v1.json", "csv_write_fallback.jsonl", "lane_pnl_ledger.json"])
def test_recovery_and_configuration_always_retained(tmp_path, name):
    put(tmp_path, name)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert result["targets"] == []
    assert result["retained"][0]["reason"].startswith("ESSENTIAL_")


@pytest.mark.parametrize("change", [{"pending_wal_records": 1}, {"pending_recovery_records": 1},
    {"pending_paper_orders": False}, {"writers_quiesced": False}, {"new_epoch_id": "epoch-old"},
    {"source_revision": "UNKNOWN"}, {"runtime_root": "elsewhere"}])
def test_missing_or_bad_proof_never_targets_indexes_or_payload(tmp_path, change):
    put(tmp_path, "v3/qualification_horizon_index.sqlite3")
    put(tmp_path, "v3/ledgers/execution.jsonl")
    p = proof(tmp_path)
    p.update(change)
    for candidate in (None, p):
        result = plan_research_reset(tmp_path, proof=candidate)
        assert not result["boundary_proof_structurally_valid"]
        assert not result["targets"]
        assert all(r["reason"] == "EPOCH_RECOVERY_BOUNDARY_PROOF_REQUIRED" for r in result["retained"])


def test_disposable_indexes_require_boundary_proof(tmp_path):
    names = ["research_events_v22.index.sqlite3", "v3/qualification_horizon_index.sqlite3",
             "v3/receipts/lifecycle_membership_v1/" + "c" * 64 + ".json"]
    for name in names:
        put(tmp_path, name)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert len(result["targets"]) == 3
    assert all(r["category"] == "RETIRED_EPOCH_DERIVED_INDEX" for r in result["targets"])


def test_archive_flattened_payload_uses_explicit_origin_not_extension(tmp_path):
    prefix = "research_archive/session_001/"
    rows = []
    for i, original in enumerate(["v3/ledgers/lifecycle.jsonl", "open_positions.json", "v3/unknown.json"]):
        preserved = f"payload/{i:06d}_" + Path(original).name
        put(tmp_path, prefix + preserved)
        rows.append({"path": original, "preserved_path": preserved, "preserved_bytes": 7,
                     "preserved_sha256": hashlib.sha256(b"record\n").hexdigest()})
    put(tmp_path, prefix + "archive_meta.json", json.dumps({"schema": "research_archive_receipt_v2",
        "source_inventory": rows}).encode())
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert [r["path"] for r in result["targets"]] == [prefix + "payload/000000_lifecycle.jsonl"]
    assert result["targets"][0]["expected_sha256"] == rows[0]["preserved_sha256"]
    assert len(result["retained"]) == 3


def test_quarantine_and_past_analysis_separate_scope(tmp_path):
    safe = "research/genome/epoch_quarantine/old/files/v3/ledgers/decision.jsonl"
    bad = "research/genome/epoch_quarantine/old/files/open_positions.json"
    derived = "past_analysis/old/executive_summary.txt"
    for name in [safe, bad, derived, "past_analysis/old/random.json", "research/genome/new_unknown.jsonl"]:
        put(tmp_path, name)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert {r["path"] for r in result["targets"]} == {safe, derived}


@pytest.mark.parametrize("limits", [{"max_entries": 1}, {"max_depth": 1}])
def test_incomplete_scan_returns_zero_targets(tmp_path, limits):
    put(tmp_path, "signal_replay.jsonl")
    put(tmp_path, "v3/ledgers/decision.jsonl")
    result = plan_research_reset(tmp_path, proof=proof(tmp_path), **limits)
    assert not result["complete"] and result["errors"]
    assert result["targets"] == []


def test_symlink_or_reparse_fail_closed(tmp_path):
    put(tmp_path, "signal_replay.jsonl")
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path / "signal_replay.jsonl")
    except OSError:
        pytest.skip("host does not permit symlink creation")
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert result["errors"] == ["UNSAFE_LINK_PRESENT"] and result["targets"] == []


def test_hardlinked_exact_path_unlink_preserves_other_link(tmp_path):
    source = put(tmp_path, "source")
    os.link(source, tmp_path / "signal_replay.jsonl")
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert [r["path"] for r in result["targets"]] == ["signal_replay.jsonl"]
    assert result["hardlinked_target_count"] == 1
    assert result["physical_bytes_reclaimed"] is None
    from research_exact_deletion import delete_exact_research_files
    paths = [r["absolute_path"] for r in result["targets"]]
    receipt = delete_exact_research_files(root=tmp_path, targets=paths, allowed_paths=paths,
        receipt_path=tmp_path / "deletion_receipt.json", quiescent=True,
        recovery_states={"wal": "EMPTY", "lifecycle": "RECONCILED"})
    assert receipt["status"] == "COMPLETE"
    assert not (tmp_path / "signal_replay.jsonl").exists()
    assert source.read_bytes() == b"record\n"


def test_plan_to_exact_deleter_all_target_classes(tmp_path):
    from research_exact_deletion import delete_exact_research_files
    names = ["v3/ledgers/execution.jsonl", "research_events_v22.index.sqlite3",
             "v3/receipts/lifecycle_membership_v1/current.json",
             "research/genome/epoch_quarantine/old/files/v3/ledgers/decision.jsonl",
             "past_analysis/old/executive_summary.txt"]
    emergency = "v3/receipts/emergency_record_idempotency_v1/lifecycle/" + "c" * 64 + ".json"
    for name in names + [emergency]:
        put(tmp_path, name)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert {r["path"] for r in result["targets"]} == set(names)
    paths = [r["absolute_path"] for r in result["targets"]]
    receipt = delete_exact_research_files(root=tmp_path, targets=paths, allowed_paths=paths,
        receipt_path=tmp_path / "deletion_receipt.json", quiescent=True,
        recovery_states={"wal": "EMPTY", "lifecycle": "RECONCILED"})
    assert receipt["status"] == "COMPLETE"
    assert (tmp_path / emergency).exists()


def test_bad_root_and_invalid_bounds_rejected(tmp_path):
    for root in (Path(tmp_path.anchor), Path.home(), Path("relative")):
        with pytest.raises(ValueError):
            plan_research_reset(root)
    with pytest.raises(ValueError):
        plan_research_reset(tmp_path, max_entries=True)


def test_reparse_attribute_rejected_without_symlink_privilege(tmp_path, monkeypatch):
    from types import SimpleNamespace
    target = put(tmp_path, "signal_replay.jsonl")
    original = Path.lstat

    def inspect(path):
        st = original(path)
        if path == target:
            return SimpleNamespace(st_mode=st.st_mode, st_file_attributes=0x400,
                st_size=st.st_size, st_mtime_ns=st.st_mtime_ns, st_dev=st.st_dev,
                st_ino=st.st_ino, st_nlink=st.st_nlink)
        return st

    monkeypatch.setattr(Path, "lstat", inspect)
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert result["errors"] == ["UNSAFE_LINK_PRESENT"]
    assert result["targets"] == [] and target.read_bytes() == b"record\n"


@pytest.mark.parametrize("variant", ["traversal", "duplicate", "wrong_size", "missing_metadata", "metadata_budget"])
def test_archive_unbound_or_ambiguous_payload_retained(tmp_path, variant):
    prefix = "research_archive/session_001/"
    put(tmp_path, prefix + "payload/000001_opportunity.jsonl")
    row = {"path": "v3/ledgers/opportunity.jsonl", "preserved_path": "payload/000001_opportunity.jsonl",
           "preserved_sha256": "c" * 64, "preserved_bytes": 7}
    if variant == "traversal":
        row["path"] = "../v3/ledgers/opportunity.jsonl"
    if variant == "wrong_size":
        row["preserved_bytes"] = 123
    rows = [row, row] if variant == "duplicate" else [row]
    if variant != "missing_metadata":
        put(tmp_path, prefix + "archive_meta.json", json.dumps({"schema": "research_archive_receipt_v2",
            "source_inventory": rows}).encode())
    args = {"max_metadata_bytes": 1} if variant == "metadata_budget" else {}
    result = plan_research_reset(tmp_path, proof=proof(tmp_path), **args)
    assert not result["targets"]


def test_config_ancestor_never_passes_deleter_scope(tmp_path):
    put(tmp_path, "past_analysis/secret-configuration/executive_summary.txt")
    result = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert not result["targets"]
    assert result["retained"][0]["reason"] == "ESSENTIAL_CONFIG_OR_CREDENTIAL"


def fake_mount(tmp_path, monkeypatch, name="research", target_name=None, nested=False):
    """Exercise the Windows junction branch without requiring symlink privilege."""
    from types import SimpleNamespace
    root = tmp_path / "runtime"
    root.mkdir(exist_ok=True)
    sibling = tmp_path / (target_name or name)
    sibling.mkdir(exist_ok=True)
    alias = root / ("nested/" + name if nested else name)
    alias.mkdir(parents=True)
    put(sibling, "signal_replay.jsonl", b"must-stay")
    # A sentinel beneath the fake alias catches accidental traversal.
    put(alias, "signal_replay.jsonl", b"must-not-traverse")
    original_lstat, original_resolve, original_readlink = Path.lstat, Path.resolve, os.readlink

    def inspect(path):
        info = original_lstat(path)
        if path == alias:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400,
                st_size=info.st_size, st_mtime_ns=info.st_mtime_ns, st_dev=info.st_dev,
                st_ino=info.st_ino, st_nlink=info.st_nlink)
        return info

    monkeypatch.setattr(Path, "lstat", inspect)
    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs:
        sibling if path == alias else original_resolve(path, *args, **kwargs))
    monkeypatch.setattr(os, "readlink", lambda path, *args, **kwargs:
        str(sibling) if Path(path) == alias else original_readlink(path, *args, **kwargs))
    return root, alias, sibling


@pytest.mark.parametrize("name", ["research", "research_accumulator", "research_archive"])
def test_exact_managed_alias_retained_not_traversed(tmp_path, monkeypatch, name):
    root, alias, sibling = fake_mount(tmp_path, monkeypatch, name)
    put(root, "signal_replay.jsonl")
    result = plan_research_reset(root, proof=proof(root), allow_fly_runtime_aliases=True)
    assert result["complete"]
    assert [r["path"] for r in result["targets"]] == ["signal_replay.jsonl"]
    row = next(r for r in result["retained"] if r["path"] == name)
    assert row["reason"] == "RETAINED_MANAGED_FLY_ALIAS_NOT_TRAVERSED"
    assert row["verified_sibling_target"] == str(sibling)
    assert (alias / "signal_replay.jsonl").read_bytes() == b"must-not-traverse"


@pytest.mark.parametrize("variant", ["default_off", "wrong_target", "unknown_name", "nested"])
def test_mismatched_managed_alias_fails_closed(tmp_path, monkeypatch, variant):
    root, _, _ = fake_mount(tmp_path, monkeypatch,
        name="unknown" if variant == "unknown_name" else "research",
        target_name="elsewhere" if variant == "wrong_target" else None,
        nested=variant == "nested")
    result = plan_research_reset(root, proof=proof(root), allow_fly_runtime_aliases=variant != "default_off")
    assert not result["complete"] and result["targets"] == []


def test_managed_alias_executor_deletes_only_runtime_payload(tmp_path, monkeypatch):
    from research_reset_execution import execute_research_reset
    root, alias, sibling = fake_mount(tmp_path, monkeypatch)
    target = put(root, "signal_replay.jsonl")
    result = execute_research_reset(runtime_root=root, proof=proof(root), quiescent=True,
        recovery_states={"wal": "EMPTY"}, receipt_path=root / "deletion.json",
        allow_fly_runtime_aliases=True)
    assert result["status"] == "COMPLETE" and not target.exists()
    assert (sibling / "signal_replay.jsonl").read_bytes() == b"must-stay"
    assert (alias / "signal_replay.jsonl").read_bytes() == b"must-not-traverse"
    assert result["retained"][0]["verified_sibling_target"] == str(sibling)


def test_real_managed_symlink_if_host_permits(tmp_path):
    root, sibling = tmp_path / "runtime", tmp_path / "research"
    root.mkdir()
    sibling.mkdir()
    put(sibling, "signal_replay.jsonl")
    try:
        (root / "research").symlink_to(sibling, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit symlink creation; deterministic reparse cases cover policy")
    result = plan_research_reset(root, proof=proof(root), allow_fly_runtime_aliases=True)
    assert result["complete"] and not result["targets"]
    assert result["retained"][0]["verified_sibling_target"] == str(sibling)


def test_archive_sibling_scope_preserves_logical_manifest_origin(tmp_path, monkeypatch):
    from research_reset_execution import execute_research_reset
    root, alias, archive = fake_mount(tmp_path, monkeypatch, name="research_archive")
    data = b"old-research\n"
    payload = put(archive, "session_001/payload/000001_opportunity.jsonl", data)
    meta = {"schema": "research_archive_receipt_v2", "source_inventory": [{
        "path": "v3/ledgers/opportunity.jsonl", "preserved_path": "payload/000001_opportunity.jsonl",
        "preserved_bytes": len(data), "preserved_sha256": hashlib.sha256(data).hexdigest()}]}
    put(archive, "session_001/archive_meta.json", json.dumps(meta).encode())
    result = plan_research_reset(root, proof=proof(root), scope_name="research_archive")
    assert result["scope_root"] == str(archive)
    assert result["runtime_root"] == str(root)
    assert result["targets"][0]["path"] == "research_archive/session_001/payload/000001_opportunity.jsonl"
    assert result["targets"][0]["category"] == "ARCHIVE_RESEARCH_PAYLOAD"
    outcome = execute_research_reset(runtime_root=root, proof=proof(root), quiescent=True,
        recovery_states={"wal": "EMPTY"}, scope_name="research_archive", receipt_path=archive / "deletion.json")
    assert outcome["status"] == "COMPLETE" and not payload.exists()
    assert alias.exists() and (archive / "session_001/archive_meta.json").exists()
    assert (alias / "signal_replay.jsonl").read_bytes() == b"must-not-traverse"


@pytest.mark.parametrize("scope,allowed,retained", [
    ("research_accumulator", ["research_trades_v983.db", "research_accumulator_status.json", "trades_accumulated.csv"],
     ["unknown.db", "source.py"]),
    ("research", ["executive_summary.txt", "reports/research_compact_summary.json"],
     ["research_dashboard.py", "genome/research.db", "genome/research.db-wal", "reports/unknown.json"]),
])
def test_scoped_sibling_known_files_only(tmp_path, monkeypatch, scope, allowed, retained):
    root, _, physical = fake_mount(tmp_path, monkeypatch, name=scope)
    for name in allowed + retained:
        put(physical, name)
    result = plan_research_reset(root, proof=proof(root), scope_name=scope)
    assert result["complete"]
    assert {r["path"] for r in result["targets"]} == {f"{scope}/{name}" for name in allowed}
    assert {f"{scope}/{name}" for name in retained} <= {r["path"] for r in result["retained"]}


def test_accumulator_database_with_sidecars_requires_separate_owner_reset(tmp_path, monkeypatch):
    root, _, physical = fake_mount(tmp_path, monkeypatch, name="research_accumulator")
    put(physical, "research_trades_v983.db")
    put(physical, "research_trades_v983.db-wal")
    result = plan_research_reset(root, proof=proof(root), scope_name="research_accumulator")
    assert not result["targets"]
    assert any(r["reason"] == "ACCUMULATOR_SQLITE_SIDECARS_REQUIRE_OWNER_RESET" for r in result["retained"])


def test_sibling_proof_cannot_be_rebased_to_arbitrary_physical_root(tmp_path, monkeypatch):
    root, _, physical = fake_mount(tmp_path, monkeypatch, name="research")
    put(physical, "executive_summary.txt")
    result = plan_research_reset(root, proof=proof(physical), scope_name="research")
    assert not result["boundary_proof_structurally_valid"] and not result["targets"]
    with pytest.raises(ValueError):
        plan_research_reset(root, proof=proof(root), scope_name="../elsewhere")


def test_sibling_scope_mismatch_rejected(tmp_path, monkeypatch):
    root, _, _ = fake_mount(tmp_path, monkeypatch, name="research", target_name="other")
    with pytest.raises(ValueError, match="scope alias mismatch"):
        plan_research_reset(root, proof=proof(root), scope_name="research")


def test_scope_alias_changed_between_plan_and_delete_is_rejected(tmp_path, monkeypatch):
    import research_reset_execution as execution
    root, alias, physical = fake_mount(tmp_path, monkeypatch, name="research")
    target = put(physical, "executive_summary.txt")
    monkeypatch.setattr(execution, "_managed_fly_alias", lambda *_: None)
    with pytest.raises(ValueError, match="RESET_PHYSICAL_SCOPE_CHANGED"):
        execution.execute_research_reset(runtime_root=root, proof=proof(root), quiescent=True,
            recovery_states={"wal": "EMPTY"}, scope_name="research", receipt_path=physical / "deletion.json")
    assert target.exists() and alias.exists()


def test_sibling_scope_nested_reparse_aborts_no_targets(tmp_path, monkeypatch):
    root, _, physical = fake_mount(tmp_path, monkeypatch, name="research")
    from types import SimpleNamespace
    nested = put(physical, "executive_summary.txt")
    original = Path.lstat

    def inspect(path):
        st = original(path)
        if path == nested:
            return SimpleNamespace(st_mode=st.st_mode, st_file_attributes=0x400, st_size=st.st_size,
                st_mtime_ns=st.st_mtime_ns, st_dev=st.st_dev, st_ino=st.st_ino, st_nlink=st.st_nlink)
        return st

    monkeypatch.setattr(Path, "lstat", inspect)
    result = plan_research_reset(root, proof=proof(root), scope_name="research")
    assert not result["complete"] and not result["targets"]


def test_validate_only_checks_real_hashes_without_any_writes(tmp_path, monkeypatch):
    import research_reset_execution as execution
    target = put(tmp_path, "signal_replay.jsonl")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(execution, "delete_exact_research_files", lambda **_: pytest.fail("preflight called deleter"))
    result = execution.execute_research_reset(runtime_root=tmp_path, proof=proof(tmp_path),
        quiescent=True, recovery_states={"wal": "EMPTY"}, receipt_path=tmp_path / "receipt.json", validate_only=True)
    assert result["status"] == "VALIDATED" and result["receipt_created"] is False
    assert result["expected_sha256_by_path"] == {str(target): hashlib.sha256(b"record\n").hexdigest()}
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_preflight_all_scopes_catches_bad_archive_before_runtime_unlink(tmp_path, monkeypatch):
    from research_reset_execution import execute_research_reset
    root, _, archive = fake_mount(tmp_path, monkeypatch, name="research_archive")
    runtime_payload = put(root, "signal_replay.jsonl")
    archived_payload = put(archive, "session_001/payload/000001_opportunity.jsonl")
    meta = {"schema": "research_archive_receipt_v2", "source_inventory": [{
        "path": "v3/ledgers/opportunity.jsonl", "preserved_path": "payload/000001_opportunity.jsonl",
        "preserved_bytes": 7, "preserved_sha256": "0" * 64}]}
    put(archive, "session_001/archive_meta.json", json.dumps(meta).encode())
    common = {"runtime_root": root, "proof": proof(root), "quiescent": True,
              "recovery_states": {"wal": "EMPTY"}, "validate_only": True}
    assert execute_research_reset(**common, allow_fly_runtime_aliases=True,
        receipt_path=root / "receipt.json")["status"] == "VALIDATED"
    with pytest.raises(ValueError, match="ARCHIVE_SHA256_MISMATCH"):
        execute_research_reset(**common, scope_name="research_archive", receipt_path=archive / "receipt.json")
    assert runtime_payload.exists() and archived_payload.exists()
    assert not (root / "receipt.json").exists() and not (archive / "receipt.json").exists()


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_validate_only_requires_explicit_bool(tmp_path, value):
    from research_reset_execution import execute_research_reset
    put(tmp_path, "signal_replay.jsonl")
    with pytest.raises(ValueError, match="VALIDATE_ONLY_REQUIRES_BOOLEAN"):
        execute_research_reset(runtime_root=tmp_path, proof=proof(tmp_path), quiescent=True,
            recovery_states={"wal": "EMPTY"}, receipt_path=tmp_path / "receipt.json", validate_only=value)


@pytest.mark.parametrize("scoped", [True, False])
def test_exact_six_genome_mirror_paths_not_database_or_code(tmp_path, monkeypatch, scoped):
    from research_genome.store import LAYER_TABLES
    from research_reset_inventory import GENOME_MIRROR_FILES
    assert GENOME_MIRROR_FILES == {name + ".jsonl" for name in LAYER_TABLES.values()}
    if scoped:
        root, _, physical = fake_mount(tmp_path, monkeypatch, name="research")
        prefix, kwargs = "genome/", {"scope_name": "research"}
    else:
        root, physical, prefix, kwargs = tmp_path, tmp_path, "research/genome/", {}
    for name in GENOME_MIRROR_FILES | {"research.db", "unknown_genome.jsonl", "store.py"}:
        put(physical, prefix + name)
    result = plan_research_reset(root, proof=proof(root), **kwargs)
    assert {r["path"] for r in result["targets"]} == {"research/genome/" + name for name in GENOME_MIRROR_FILES}
