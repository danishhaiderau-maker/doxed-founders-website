import pytest

from crash_journal_repair import digest, plan_repair, preserve_plan


def plan(raw):
    return plan_repair(raw, expected_size=len(raw), expected_sha256=digest(raw))


def test_interior_corruption_preserves_original_and_valid_bytes():
    raw = b'{"a": 1}\n{"broken"\n{"b":2}\n'
    original, derived, receipt = plan(raw)
    assert original == raw
    assert derived == b'{"a": 1}\n{"b":2}\n'
    excluded = receipt['excluded_records'][0]
    assert raw[excluded['offset']:excluded['offset'] + excluded['size']] == b'{"broken"\n'
    assert excluded['sha256'] == digest(b'{"broken"\n')
    assert excluded['classification'] == 'UNKNOWN'
    assert receipt['valid_records'] == 2


@pytest.mark.parametrize('bad', [b'{', b'null', b'[]', b'{"a":NaN}', b'\xff'])
def test_unsupported_records_explicitly_excluded(bad):
    assert len(plan(b'{}\n' + bad + b'\n')[2]['excluded_records']) == 1


def test_changed_source_refused():
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        plan_repair(b'{}\n{\n', expected_size=6, expected_sha256='0' * 64)


@pytest.mark.parametrize('raw', [b'', b'{}\n{', b'{}\n', b'{\n'])
def test_nonincident_or_unsupported_shape_refused(raw):
    with pytest.raises(ValueError):
        plan(raw)


def test_preserve_creates_verified_original_and_derived_without_overwrite(tmp_path):
    import json
    raw = b'{}\n{\n'
    target = tmp_path / 'forensic'
    manifest = preserve_plan(raw, target, expected_size=len(raw), expected_sha256=digest(raw))
    assert (target / 'original.bin').read_bytes() == raw
    assert (target / 'derived.jsonl').read_bytes() == b'{}\n'
    assert json.loads(manifest.read_text())['source_sha256'] == digest(raw)
    with pytest.raises(FileExistsError):
        preserve_plan(raw, target, expected_size=len(raw), expected_sha256=digest(raw))
    assert (target / 'original.bin').read_bytes() == raw


def test_preserve_rejects_source_change_before_creating_directory(tmp_path):
    target = tmp_path / 'forensic'
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        preserve_plan(b'{}\n{\n', target, expected_size=1, expected_sha256='0' * 64)
    assert not target.exists()


def test_startup_exact_repair_and_repeat_preserves_archive(tmp_path, monkeypatch):
    import crash_journal_repair as module
    raw = b'{}\n{\n'
    monkeypatch.setattr(module, 'INCIDENT_SIZE', len(raw))
    monkeypatch.setattr(module, 'INCIDENT_SHA256', digest(raw))
    target = tmp_path / 'crash_dump.json'
    target.write_bytes(raw)
    assert module.repair_known_incident_at_startup(tmp_path) == 'REPAIRED_EXACT_INCIDENT'
    assert target.read_bytes() == b'{}\n'
    assert module.repair_known_incident_at_startup(tmp_path) == 'NOT_EXACT_INCIDENT'
    assert next((tmp_path / 'corrupt_evidence_quarantine').glob('*/original.bin')).read_bytes() == raw


@pytest.mark.parametrize('stage', ['empty', 'original', 'derived', 'manifest'])
def test_startup_resumes_interrupted_preservation(tmp_path, monkeypatch, stage):
    import crash_journal_repair as module
    raw = b'{}\n{\n'
    monkeypatch.setattr(module, 'INCIDENT_SIZE', len(raw))
    monkeypatch.setattr(module, 'INCIDENT_SHA256', digest(raw))
    target = tmp_path / 'crash_dump.json'
    target.write_bytes(raw)
    archive = tmp_path / 'corrupt_evidence_quarantine' / ('crash-journal-' + digest(raw))
    archive.mkdir(parents=True)
    if stage != 'empty':
        (archive / 'original.bin').write_bytes(raw[:2] if stage == 'original' else raw)
    if stage in ('derived', 'manifest'):
        (archive / 'derived.jsonl').write_bytes(b'{' if stage == 'derived' else b'{}\n')
    if stage == 'manifest':
        (archive / 'manifest.json').write_bytes(b'{')
    assert module.repair_known_incident_at_startup(tmp_path) == 'REPAIRED_EXACT_INCIDENT'
    assert (archive / 'original.bin').read_bytes() == raw
    assert target.read_bytes() == b'{}\n'


def test_resume_never_overwrites_conflicting_evidence(tmp_path):
    from crash_journal_repair import _resume_preserved_file
    target = tmp_path / 'original.bin'
    target.write_bytes(b'conflict')
    with pytest.raises(ValueError, match='MISMATCH'):
        _resume_preserved_file(target, b'expected')
    assert target.read_bytes() == b'conflict'


def test_bot_startup_repair_follows_singleton_and_precedes_service_threads():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    calls = list(n for n in ast.walk(main) if isinstance(n, ast.Call))
    singleton = next(n for n in calls if isinstance(n.func, ast.Name) and n.func.id == 'acquire_process_singleton')
    repair = next(n for n in calls if isinstance(n.func, ast.Name) and n.func.id == 'repair_known_incident_at_startup')
    starts = [n for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == 'start']
    assert singleton.lineno < repair.lineno < min(n.lineno for n in starts)
    owner_try = next(n for n in main.body if isinstance(n, ast.Try)
                     and singleton in list(ast.walk(n)))
    assert any(isinstance(n, ast.Raise) for handler in owner_try.handlers
               for n in ast.walk(handler))


@pytest.mark.parametrize('linked_name', ['original.bin', 'derived.jsonl', 'manifest.json'])
def test_startup_rejects_linked_archive_artifacts(tmp_path, monkeypatch, linked_name):
    import crash_journal_repair as module
    raw = b'{}\n{\n'
    monkeypatch.setattr(module, 'INCIDENT_SIZE', len(raw))
    monkeypatch.setattr(module, 'INCIDENT_SHA256', digest(raw))
    target = tmp_path / 'crash_dump.json'
    target.write_bytes(raw)
    quarantine = tmp_path / 'corrupt_evidence_quarantine'
    quarantine.mkdir()
    archive = quarantine / ('crash-journal-' + digest(raw))
    preserve_plan(raw, archive, expected_size=len(raw), expected_sha256=digest(raw))
    artifact = archive / linked_name
    outside = tmp_path / ('outside-' + linked_name)
    artifact.rename(outside)
    try:
        artifact.symlink_to(outside)
    except OSError as error:
        pytest.skip('Host cannot create symlinks: ' + str(error))
    with pytest.raises(ValueError, match='LINKED_ARTIFACT'):
        module.repair_known_incident_at_startup(tmp_path)
    assert target.read_bytes() == raw
