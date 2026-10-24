"""RK PASS 24 Oct: after a real delete-by-id, the in-memory trade list is reloaded from CSV_TRADES under the
trade-file lock, and a later save cannot bring a deleted id back."""
import ast, csv, logging, pathlib, threading

import trade_delete_by_id as m
from test_trade_delete_by_id import ALL, DEL, _ids, _mk

SRC = pathlib.Path(__file__).with_name("bot.py").read_text()


def _ns(path):
    tree = ast.parse(SRC)
    want = {"_reload_trades_after_delete", "dynamic_csv_writer"}
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in want)
            or (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "_DELETED_TRADE_IDS")]
    assert len(body) == 3
    ns = {"threading": threading, "logger": logging.getLogger("t"), "CSV_TRADES": path, "csv_lock": threading.RLock(),
          "_research_write_gate": threading.RLock(), "trade_lock": threading.RLock(), "trades": [],
          "CSV_WRITE_RETRIES": 1, "_csv_fallback_pending_targets": set(), "state": {}}

    def load():  # same contract as load_session_trades_from_csv: add CSV rows missing from memory
        have = {t["trade_id"] for t in ns["trades"]}
        with open(path, newline="") as f:
            ns["trades"].extend(r for r in csv.DictReader(f) if r["trade_id"] not in have)

    def write_once(fn, row):
        with open(fn, "a", newline="") as f:
            csv.writer(f).writerow([row["trade_id"], row.get("pnl", ""), row.get("note", "")])

    ns.update(load_session_trades_from_csv=load, _dynamic_csv_writer_once=write_once)
    exec(compile(ast.Module(body=body, type_ignores=[]), "bot.py", "exec"), ns)
    load()
    return ns


def test_later_save_cannot_resurrect_deleted_id(tmp_path):
    p = _mk(tmp_path, ALL)
    ns = _ns(p)
    stale = [dict(t) for t in ns["trades"] if t["trade_id"] in DEL]
    m.delete_trades_by_id(p, DEL, m.list_sha256(DEL), dry_run=False, lock=threading.RLock())
    assert ns["_reload_trades_after_delete"](DEL) == 3
    assert [t["trade_id"] for t in ns["trades"]] == [t for t in ALL if t not in DEL]
    for row in stale:  # e.g. a patch/re-log of a row that was still held in memory
        ns["dynamic_csv_writer"](p, row)
    ns["dynamic_csv_writer"](p, {"trade_id": "t-new"})
    assert _ids(p) == [t for t in ALL if t not in DEL] + ["t-new"]
    ns["load_session_trades_from_csv"]()
    assert not {t["trade_id"] for t in ns["trades"]} & set(DEL)


def test_route_reloads_only_after_real_delete_under_lock():
    i = SRC.index("def api_admin_trades_delete_by_id")
    route = SRC[i:SRC.index("@app.route", i)]
    w, d, r = (route.index("with _TradeFileLock():"), route.index("_tdi.delete_trades_by_id("),
               route.index("_reload_trades_after_delete("))
    assert w < d < r
    assert 'if not out.get("dry_run") and out.get("removed_count"):' in route[d:r]
