"""Read-only :9001 History & retention view: long-horizon archive, schema compat, laptop retention.

Rows come from the immutable analysis archive and the current generation's
``long_horizon_report.json``; nothing here can create orders or delete data.
"""
from __future__ import annotations

import html
from typing import Iterable, Optional

LONG_HORIZON_FILE = "long_horizon_report.json"


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _num(value, digits: int = 4) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return _esc(value)


def _gb(value) -> str:
    try:
        return f"{float(value) / 1e9:.2f} GB"
    except (TypeError, ValueError):
        return "-"


def _table(headers: Iterable[str], rows: list) -> str:
    if not rows:
        return "<p class='sub'>No rows.</p>"
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>"


def compat_section(compat: Optional[dict]) -> str:
    """Schema-compat table shared by the history and data-health pages."""
    compat = compat or {}
    counts = compat.get("schema_compat") or {}
    rows = [[_esc(dataset), _esc(cells.get("COMPATIBLE", 0)), _esc(cells.get("CONVERTED", 0)),
             _esc(cells.get("INCOMPATIBLE", 0)), _esc(cells.get("TAMPERED", 0))]
            for dataset, cells in sorted(counts.items())]
    bad = compat.get("incompatible") or []
    note = (f"<p style='color:#f85149'>{len(bad)} document(s) INCOMPATIBLE/TAMPERED: excluded from analysis; "
            "incompatible ones move to legacy/ and are deleted first under the 50 GB cap.</p>" if bad
            else "<p class='sub'>Every archived document has a supported schema_version.</p>")
    return _table(["dataset", "compatible", "converted", "incompatible", "tampered"], rows) + note


def render_archive_html(archive: Optional[dict], long_horizon: Optional[dict], *, status: str,
                        reason: Optional[str], nav_links) -> str:
    archive = archive or {}
    long_horizon = long_horizon if isinstance(long_horizon, dict) else {}
    latest = archive.get("latest_snapshot") or {}
    retention = archive.get("retention") or {}
    ok = status == "OK"
    banner = (f"Archive {status}: latest snapshot {_esc(latest.get('snapshot_id'))} written "
              f"{_esc(latest.get('written_at'))}, segment seq through {_esc(latest.get('segment_seq_through'))}"
              if ok else f"Archive {_esc(status)}: {_esc(reason or 'unavailable')}")
    colour = "#3fb950" if ok else "#f85149"
    coverage = long_horizon.get("coverage") or {}
    tiles = long_horizon.get("tiles") or {}
    tile_rows = []
    for name, row in sorted(tiles.items(), key=lambda kv: -((kv[1] or {}).get("n") or 0)):
        trend = row.get("daily_mean_trend") or {}
        tile_rows.append([_esc(name), _esc(row.get("n")), _esc(row.get("days")), _num(row.get("net_pnl_usd"), 2),
                          _num(row.get("mean_usd")),
                          f"[{_num(row.get('ci95_lo_usd'))}, {_num(row.get('ci95_hi_usd'))}]",
                          _num(row.get("win_rate"), 3), _esc(trend.get("direction") or "-"),
                          _esc(row.get("suggestion") or "")])
    families = long_horizon.get("families") or {}
    family_rows = [[_esc(name), _esc(row.get("n")), _num(row.get("net_pnl_usd"), 2), _num(row.get("mean_usd")),
                    f"[{_num(row.get('ci95_lo_usd'))}, {_num(row.get('ci95_hi_usd'))}]", _num(row.get("t_stat"), 2)]
                   for name, row in sorted(families.items(), key=lambda kv: -((kv[1] or {}).get("n") or 0))]
    recent_rows = [[_esc(row.get("key")), _esc(row.get("days")), _esc(row.get("n")),
                    _num(row.get("net_pnl_usd"), 2), _num(row.get("mean_usd_last_7d")),
                    _num(row.get("mean_usd_before"))]
                   for row in archive.get("tiles_long_horizon") or []]
    usage = retention.get("usage_fraction")
    retention_rows = [[_esc(retention.get("configured_mode")), _esc(retention.get("mode")),
                       _esc(retention.get("finished_at")), _esc(retention.get("level")),
                       f"{_gb(retention.get('bytes_after'))} / {_gb(retention.get('cap_bytes'))}"
                       + (f" ({float(usage) * 100:.1f}%)" if usage is not None else ""),
                       _gb(retention.get("reclaimed_bytes")), _gb(retention.get("would_reclaim_bytes")),
                       _esc(", ".join(retention.get("deny_reasons") or []) or "none"),
                       _esc(retention.get("ledger_rows"))]]
    nav = " · ".join(f"<a href='{_esc(href)}'>{_esc(label)}</a>" for label, href in nav_links)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>History &amp; retention</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:Segoe UI,Arial,sans-serif;margin:16px;}}
a{{color:#58a6ff}} table{{border-collapse:collapse;font-size:0.85em;margin:6px 0 14px 0}}
th,td{{border:1px solid #30363d;padding:4px 8px;text-align:left;vertical-align:top}}
th{{background:#161b22}} .sub{{color:#8b949e;font-size:0.85em}} .wrap{{overflow:auto}}
</style></head><body>
<nav class='sub'>{nav}</nav>
<h1>History &amp; retention</h1>
<p id='archiveBanner' style='color:{colour};font-weight:600'>{banner}</p>
<p class='sub'>Archive root {_esc(archive.get('root'))} · immutable per-generation snapshots + daily/weekly rollups,
never auto-deleted · long-horizon coverage {_esc(coverage.get('days_total'))} day(s)
({_esc(coverage.get('days_from_raw'))} raw, {_esc(coverage.get('days_from_archive'))} archive-only),
{_esc(coverage.get('first_day'))} to {_esc(coverage.get('last_day'))}</p>
<h2>Long-horizon tiles (archive + current raw, deduplicated per day and epoch)</h2>
{_table(["tile", "n", "days", "net $", "mean $", "95% CI $", "win rate", "daily trend", "suggestion"], tile_rows)}
<h2>Long-horizon exit families</h2>
{_table(["family", "n", "net $", "mean $", "95% CI $", "t"], family_rows)}
<h2>Last 30 days from rollups (last 7 d vs before)</h2>
{_table(["tile", "days", "n", "net $", "mean $ last 7 d", "mean $ before"], recent_rows)}
<h2>Schema compatibility</h2>
{compat_section(archive)}
<h2>Laptop retention (50 GB cap)</h2>
{_table(["configured", "last run mode", "finished", "level", "managed usage", "reclaimed", "would reclaim",
         "deny reasons", "ledger rows"], retention_rows)}
<p class='sub'>Deletion requires Fly ACK + hash parity + analyzer consumption + a verified snapshot covering the
window; every deletion is in the append-only prune ledger. Read-only page, no orders.</p>
</body></html>"""
