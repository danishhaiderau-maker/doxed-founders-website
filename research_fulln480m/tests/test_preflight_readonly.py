"""BP tests: P1 read-only preflight on a synthetic laptop tree (box only; the script is NEVER run on the laptop here).
Covers RK c07fa806dfad (2) terms, plan r3 s7 P1 mutants (extra path, overwrite, network, unredacted CommandLine,
SilentlyContinue on L106) and the RK f83568141488 Q-R1 freeze form."""
import ast
import hashlib
import inspect
import json
import ntpath
import os
import socket

import pytest

from research_fulln480m import preflight_readonly_20261114 as pf

CARD = "/workspace/grok-strategist/reports/GS-CARD-BTC-FULLN-480M-LAUNCH-r3-20261114.md"
SECRET = "tok_SECRETVALUE_123"


class _Dist(object):
    def __init__(self, name, version):
        self.metadata = {"Name": name}
        self.version = version


DISTS = [_Dist("numpy", "1.26.4"), _Dist("SciPy", "1.11.4"), _Dist("PyYAML", "6.0.1"), _Dist("pandas", "2.1.4")]


def _ops():
    return [(lid, pf.parse_line(lid, body)) for lid, body in pf.load_lines()]


def _tree(tmp_path, extra=None):
    root_c = tmp_path / "C"
    root_u = tmp_path / "U"
    pmap = (("C:\\DoxxedCrypto", str(root_c)), ("C:\\Users\\danis", str(root_u)))
    cfg_host = pf.Config(path_map=pmap).host
    for lid, op in _ops():
        if op["kind"] in ("listing", "hash_wild"):
            os.makedirs(cfg_host(op["dir"]), exist_ok=True)
        elif op["kind"] in ("hash_lit", "select_string", "item"):
            h = cfg_host(op["path"])
            os.makedirs(os.path.dirname(h), exist_ok=True)
            if not os.path.exists(h):
                with open(h, "wb") as f:
                    f.write(b"import os\nfrom x import y\n# external-data\ndef load_a():\n    pass\n")
    files = {
        "C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0\\trades\\2024-10-30.jsonl.gz": b"t30",
        "C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0\\trades\\2024-10-31.jsonl.gz": b"t31x",
        "C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0\\candles1m\\2024-10-30.jsonl.gz": b"c30",
        "C:\\DoxxedCrypto\\external\\ext2\\external-data\\binance\\futures\\um\\daily\\metrics\\BTCUSDT\\BTCUSDT-metrics-2024-11-01.zip": b"m",
        "C:\\DoxxedCrypto\\external\\ext2\\external-data\\binance\\futures\\um\\monthly\\fundingRate\\BTCUSDT\\BTCUSDT-fundingRate-2024-11.zip": b"fr",
        "C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex-stats1\\funding_stats_fBTC.raw.jsonl": b"fs",
        "C:\\DoxxedCrypto\\external\\ext2\\code\\gpu_kernel.py": b"import numpy\n",
    }
    files.update(extra or {})
    for w, b in files.items():
        h = cfg_host(w)
        os.makedirs(os.path.dirname(h), exist_ok=True)
        with open(h, "wb") as f:
            f.write(b)
    os.makedirs(cfg_host("C:\\Users\\danis\\AppData\\Local\\Programs\\Python\\Python311\\Lib\\site-packages\\numpy-1.26.4.dist-info"), exist_ok=True)
    os.makedirs(cfg_host(pf.PREFLIGHT_DIR), exist_ok=True)
    return pmap, cfg_host, files


def _runner(overrides=None):
    calls = []

    def run(body):
        calls.append(body)
        if overrides and body in overrides:
            return overrides[body]
        if body.startswith("Get-NetTCPConnection"):
            return 1, "", "Get-NetTCPConnection : No matching MSFT_NetTCPConnection objects found by CIM query"
        if "Win32_Process " in body or "'python.exe'" in body:
            return 0, ("\nProcessId CommandLine\n--------- -----------\n     5820 C:\\py\\python.exe a.py --token=%s\n"
                       "    11004 python.exe -c \"key=%s\"\n" % (SECRET, SECRET)), ""
        return 0, "\nName : stub\n", ""
    run.calls = calls
    return run


def _cfg(pmap, **kw):
    ft = pf.freeze_text(DISTS)
    args = dict(path_map=pmap, system_runner=_runner(), distributions=DISTS,
                expected_freeze_sha256=hashlib.sha256(ft).hexdigest())
    args.update(kw)
    return pf.Config(**args)


def _receipt(cfg_host):
    p = os.path.join(cfg_host(pf.PREFLIGHT_DIR), pf.RECEIPT_NAME)
    with open(p, "rb") as f:
        data = f.read()
    return data, json.loads(data)


# ---------------------------------------------------------------- the literal table
def test_p1_lines_are_card_r3_verbatim():
    lines = pf.load_lines()
    assert len(lines) == 310 and [x[0] for x in lines] == pf.expected_ids()
    assert hashlib.sha256(pf.LINES_TEXT.encode()).hexdigest() == pf.LINES_SHA256
    if os.path.exists(CARD):  # box-only cross-check against the bound card bytes
        with open(CARD, "rb") as f:
            card = f.read()
        assert hashlib.sha256(card).hexdigest() == pf.CARD_SHA256
        want = [ln for ln in card.decode("utf-8").split("\n")
                if len(ln) > 6 and ln[0] in "LBC" and ln[1:4].isdigit() and ln[4:6] == "  "]
        assert want == pf.LINES_TEXT.rstrip("\n").split("\n")
    kinds = {lid: op["kind"] for lid, op in _ops()}
    assert [k for k, v in kinds.items() if v == "system"] == list(pf.SYSTEM_LINE_IDS)
    l106 = dict(lines)["L106"]
    assert "SilentlyContinue" not in l106 and "-State" not in l106


def test_p1_extra_or_edited_line_refused():
    with pytest.raises(pf.PreflightStop):
        pf.load_lines(pf.LINES_TEXT + "L127  Get-Content -LiteralPath 'C:\\Users\\danis\\.ssh\\id_rsa'\n")
    edited = pf.LINES_TEXT.replace("2024-10-*.jsonl.gz", "*.jsonl.gz", 1)
    with pytest.raises(pf.PreflightStop):
        pf.load_lines(edited)
    with pytest.raises(pf.PreflightStop):
        pf.parse_line("L999", "Get-Content -LiteralPath 'C:\\x'")
    with pytest.raises(pf.PreflightStop):
        pf.parse_line("B999", "Get-FileHash -Algorithm SHA256 -Path 'C:\\a\\*\\b' | Select-Object -Property Hash,Path")


# ---------------------------------------------------------------- full run on a synthetic tree
def test_p1_full_run_receipt(tmp_path):
    pmap, host, files = _tree(tmp_path)
    cfg = _cfg(pmap)
    assert pf.run(cfg) == 0
    data, rec = _receipt(host)
    assert len(data) <= 5_000_000 and rec["stops"] == [] and rec["schema"] == pf.SCHEMA
    assert [x["id"] for x in rec["lines"]] == pf.expected_ids()
    by = {f["path"]: f for f in rec["files"]}
    for w, b in files.items():
        if w.endswith("gpu_kernel.py"):
            continue
        assert by[w]["sha256"] == hashlib.sha256(b).hexdigest() and by[w]["length"] == len(b)
    assert by["C:\\DoxxedCrypto\\external\\ext2\\code\\gpu_kernel.py"]["sha256"] == hashlib.sha256(b"import numpy\n").hexdigest()
    # manifests recomputed from the receipt text alone (box re-computation, card r3 s13)
    lines = sorted(("%s %d %s" % (f["path"][len(pf.EXTERNAL_DATA) + 1:].replace("\\", "/"), f["length"], f["sha256"])).encode()
                   for f in rec["files"] if f["path"].startswith(pf.EXTERNAL_DATA + "\\"))
    assert rec["manifests"]["ALL"] == hashlib.sha256(b"".join(x + b"\n" for x in lines)).hexdigest()
    assert "bitfinex/tBTCF0USTF0/trades" in rec["manifests"]
    l106 = [x for x in rec["lines"] if x["id"] == "L106"][0]
    assert l106["status"] == "NOT_LISTENING"
    assert SECRET.encode() not in data and b"--token" not in data
    l125 = [x for x in rec["lines"] if x["id"] == "L125"][0]
    assert "5820 [REDACTED]" in l125["stdout"] and "11004 [REDACTED]" in l125["stdout"]
    assert rec["freeze"]["sha256"] == cfg.expected_freeze_sha256
    assert sorted(os.listdir(host(pf.PREFLIGHT_DIR))) == sorted([pf.RUN_MARKER, pf.RECEIPT_NAME])
    sel = [x for x in rec["lines"] if x["id"] == "C001"][0]
    assert [r["LineNumber"] for r in sel["rows"]] == [1, 2]
    l123 = [x for x in rec["lines"] if x["id"] == "L123"][0]
    assert l123["rows"] == [{"Value": True}]  # the fixture's RUN_DIR exists (it holds preflight\\)


def test_p1_one_run_only_no_overwrite(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    assert pf.run(_cfg(pmap)) == 0
    before = open(os.path.join(host(pf.PREFLIGHT_DIR), pf.RECEIPT_NAME), "rb").read()
    with pytest.raises(pf.PreflightStop):
        pf.run(_cfg(pmap))
    assert open(os.path.join(host(pf.PREFLIGHT_DIR), pf.RECEIPT_NAME), "rb").read() == before


def test_p1_preflight_dir_must_exist(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    os.rename(host(pf.PREFLIGHT_DIR), host(pf.PREFLIGHT_DIR) + "-moved-by-test")
    with pytest.raises(pf.PreflightStop):
        pf.run(_cfg(pmap))


def test_p1_reader_refuses_non_literal_paths(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    rd = pf.Reader(pf.Config(path_map=pmap), _ops())
    with pytest.raises(pf.PreflightStop):
        rd.read_bytes_sha("C:\\DoxxedCrypto\\external\\ext2\\code\\other.py")
    with pytest.raises(pf.PreflightStop):
        rd.read_bytes_sha("C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0\\trades\\2024-10-30.json")
    with pytest.raises(pf.PreflightStop):
        rd.list_dir("C:\\DoxxedCrypto\\data")
    n, h = rd.read_bytes_sha("C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0\\trades\\2024-10-30.jsonl.gz")
    assert n == 3


def test_p1_missing_directory_stops(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    d = host("C:\\DoxxedCrypto\\external\\ext2\\external-data\\binance\\spot\\daily\\aggTrades\\BTCUSDT")
    os.rename(d, d + "-absent")
    assert pf.run(_cfg(pmap)) == 78
    data, rec = _receipt(host)
    assert rec["stops"] and "absent" in rec["stops"][0]


# ---------------------------------------------------------------- network / writes
def test_p1_network_refused(tmp_path):
    pmap, host, _ = _tree(tmp_path)

    def evil(body):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        return 0, "x", ""
    assert pf.run(_cfg(pmap, system_runner=evil)) == 78
    _, rec = _receipt(host)
    assert "network" in rec["stops"][0]
    tree = ast.parse(inspect.getsource(pf))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert not mods & {"socket", "urllib", "http", "requests", "ssl", "ftplib", "smtplib", "asyncio", "xmlrpc", "httpx"}


def test_p1_writes_only_new_files_in_preflight(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    target = str(tmp_path / "outside.txt")

    def writer(body):
        with open(target, "w") as f:
            f.write("x")
        return 0, "x", ""
    assert pf.run(_cfg(pmap, system_runner=writer)) == 78
    assert not os.path.exists(target)
    pmap2, host2, _ = _tree(tmp_path / "second")
    inside = os.path.join(host2(pf.PREFLIGHT_DIR), "extra.txt")

    def overwrite(body):
        with open(inside, "w") as f:  # not exclusive
            f.write("x")
        return 0, "x", ""
    assert pf.run(_cfg(pmap2, system_runner=overwrite)) == 78
    assert not os.path.exists(inside)


def test_p1_process_start_refused(tmp_path):
    import shutil
    import subprocess
    pmap, host, _ = _tree(tmp_path)
    exe = shutil.which("true") or shutil.which("hostname")

    def spawner(body):
        subprocess.run([exe], capture_output=True)
        return 0, "x", ""
    assert pf.run(_cfg(pmap, system_runner=spawner)) == 78
    _, rec = _receipt(host)
    assert "process start refused" in rec["stops"][0]


# ---------------------------------------------------------------- L106 / L125
def test_p1_l106_listen_stops(tmp_path):
    l106 = dict(pf.load_lines())["L106"]
    pmap, host, _ = _tree(tmp_path)
    r = _runner({l106: (0, "LocalAddress LocalPort State OwningProcess\n0.0.0.0 7002 Listen 4321\n", "")})
    assert pf.run(_cfg(pmap, system_runner=r)) == 78
    _, rec = _receipt(host)
    assert "LISTEN" in rec["stops"][0]


def test_p1_l106_silently_continue_shape_stops():
    cfg = pf.Config(system_runner=lambda b: (0, "", ""))
    with pytest.raises(pf.PreflightStop):
        pf._do_system(cfg, "L106", "x")
    cfg2 = pf.Config(system_runner=lambda b: (1, "", "Access denied"))
    with pytest.raises(pf.PreflightStop):
        pf._do_system(cfg2, "L106", "x")
    cfg3 = pf.Config(system_runner=lambda b: (0, "0.0.0.0 7002 TimeWait 0\n", ""))
    assert pf._do_system(cfg3, "L106", "x")["status"] == "ROWS_NOT_LISTEN"
    cfg4 = pf.Config(system_runner=lambda b: (1, "", "x"))
    with pytest.raises(pf.PreflightStop):
        pf._do_system(cfg4, "L101", "x")


def test_p1_l125_redaction():
    out = pf.redact_l125("\nProcessId CommandLine\n--------- -----------\n  42 python.exe --api-key=ABC\nwrapped tail ABC\n")
    assert "ABC" not in out and "42 [REDACTED]" in out and "ProcessId CommandLine" in out
    r = pf._do_system(pf.Config(system_runner=lambda b: (0, "1 x SECRET\nSECRET tail", "")), "L125", "x")
    assert "SECRET" not in r["stdout"] and r["stdout"] == "1 [REDACTED]\n[REDACTED]"
    with pytest.raises(pf.PreflightStop) as ei:
        pf._do_system(pf.Config(system_runner=lambda b: (0, "1 x", "err SECRET")), "L125", "x")
    assert "SECRET" not in ei.value.reason


# ---------------------------------------------------------------- denylist
def test_p1_denylist_r62h():
    for p in ("C:\\x\\.env", "C:\\x\\.env.local", "C:\\x\\a.PEM", "C:\\x\\k.key", "C:\\x\\c.pfx", "C:\\x\\c.p12",
              "C:\\Users\\danis\\.ssh\\config", "C:\\x\\id_rsa.pub", "C:\\x\\id_ed25519", "C:\\x\\.git-credentials",
              "C:\\x\\.netrc", "C:\\x\\.npmrc", "C:\\x\\.pypirc", "C:\\x\\my_SECRET.txt", "C:\\x\\api_token.json",
              "C:\\x\\Credentials\\a", "C:\\x\\ApiKey.cfg", "C:\\Users\\danis\\AppData\\Local\\Google\\Chrome\\User Data\\x",
              "C:\\Users\\danis\\.aws\\credentials", "C:\\Users\\danis\\.config\\gh\\hosts.yml", "C:\\x\\bitfinex_live.json",
              "C:\\x\\live-key.dat"):
        assert pf.is_denied(p), p
    for p in ("C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0\\trades\\2024-10-30.jsonl.gz",
              "C:\\DoxxedCrypto\\external\\ext2\\code\\ext2_search.py",
              "C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex-stats1\\funding_stats_fBTC.raw.jsonl"):
        assert not pf.is_denied(p), p
    assert not [b for _, b in pf.load_lines() if "'" in b and pf.is_denied(b.split("'")[1])]


def test_p1_denylisted_file_in_hashed_dir_stops(tmp_path):
    pmap, host, _ = _tree(tmp_path, {"C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex-stats1\\api_token.txt": b"zz"})
    assert pf.run(_cfg(pmap)) == 78
    data, rec = _receipt(host)
    assert "denylisted" in rec["stops"][0] and b"zz" not in data


# ---------------------------------------------------------------- Q-R1 freeze form
def test_p1_freeze_form():
    ft = pf.freeze_text([_Dist("a", "1"), _Dist("A-B", "1"), _Dist("Zope.Interface", "5"), _Dist("na\u00efve", "2")])
    lines = ft.split(b"\n")
    assert lines[0].startswith(b"#interpreter version=") and b" executable=" in lines[0]
    assert lines[-1] == b"" and b"\r" not in ft
    body = lines[1:-1]
    assert body == sorted(body) and body[0] == b"a-b==1" and body[1] == b"a==1"
    assert b"zope-interface==5" in body and "na\u00efve==2".encode("utf-8") in body
    ft.decode("utf-8")
    with pytest.raises(pf.PreflightStop):
        pf.freeze_text([_Dist("numpy", "1"), _Dist("NumPy", "2")])
    with pytest.raises(pf.PreflightStop):
        pf.freeze_text([_Dist("py_yaml", "1"), _Dist("py.yaml", "1")])
    try:  # the real interpreter: either a clean freeze text or a duplicate-distribution STOP (both fail-closed)
        real = pf.freeze_text()
        assert real.split(b"\n")[0].endswith(os.fsencode(__import__("sys").executable))
    except pf.PreflightStop as e:
        assert "duplicate distributions" in e.reason


def test_p1_freeze_mismatch_or_unpinned_stops(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    assert pf.run(_cfg(pmap, expected_freeze_sha256="0" * 64)) == 78
    _, rec = _receipt(host)
    assert "freeze sha mismatch" in rec["stops"][0] and rec["lines"] == []
    pmap2, host2, _ = _tree(tmp_path / "b")
    assert pf.run(_cfg(pmap2, expected_freeze_sha256=None)) == 78
    _, rec2 = _receipt(host2)
    assert "not pinned" in rec2["stops"][0]
    assert pf.EXPECTED_FREEZE_SHA256 is None


# ---------------------------------------------------------------- size cap / pairing / main
def test_p1_receipt_over_cap_no_truncation(tmp_path):
    pmap, host, _ = _tree(tmp_path)
    assert pf.run(_cfg(pmap, receipt_max_bytes=10_000)) == 78
    names = os.listdir(host(pf.PREFLIGHT_DIR))
    assert pf.RECEIPT_NAME not in names and pf.STOP_NOTE_NAME in names
    note = json.loads(open(os.path.join(host(pf.PREFLIGHT_DIR), pf.STOP_NOTE_NAME)).read())
    assert "no truncation path" in note["stops"][0]


def test_p1_hash_listing_count_must_match():
    ops = [("L001", {"kind": "listing", "dir": "C:\\d", "only": "-File", "filter": "a*", "props": ["Name", "Length"]}),
           ("L051", {"kind": "hash_wild", "dir": "C:\\d", "filter": "a*"})]
    res = {"L001": {"rows": [{"Name": "a1", "Length": 1}], "count": 1},
           "L051": {"rows": [{"Hash": "AB" * 32, "Path": "C:\\d\\a1", "_length": 1},
                             {"Hash": "CD" * 32, "Path": "C:\\d\\a2", "_length": 1}], "count": 2}}
    with pytest.raises(pf.PreflightStop):
        pf.pair_listing_and_hash(ops, res)
    res["L001"] = {"rows": [{"Name": "a1", "Length": 1}, {"Name": "a2", "Length": 9}], "count": 2}
    with pytest.raises(pf.PreflightStop):
        pf.pair_listing_and_hash(ops, res)
    res["L001"]["rows"][1]["Length"] = 1
    assert len(pf.pair_listing_and_hash(ops, res)) == 2
    res["L051"] = {"rows": [{"Hash": "AB" * 32, "Path": "C:\\d\\a1", "_length": 1}], "count": 1}  # hash missed a file
    with pytest.raises(pf.PreflightStop):
        pf.pair_listing_and_hash(ops, res)


def test_p1_main_refuses_args_and_non_windows():
    assert pf.main(["--anything"]) == 78
    if os.name != "nt":
        assert pf.main([]) == 78
    pc = pf.production_config()
    assert pc.production and pc.system_runner is pf.powershell_runner and pc.expected_freeze_sha256 is None
    assert pf._ps_argv("Get-Host | Select-Object -Property Name,Version")[:4] == (
        pf.POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command")
