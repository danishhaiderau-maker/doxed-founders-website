"""Synthetic capture session. No socket, no clock read, no second output root."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import (
    CaptureStopped,
    ConfigUnset,
    StartRefused,
    backoff_delay,
    check_instrument,
    classify_clock_offset,
    require_set,
)
from .framing import (
    RAW_KEYS,
    AppendJournal,
    SegmentFile,
    confine,
    decode_prefix,
    dumps,
    read_journal,
    sha256_bytes,
)
from .market import FeedState, classify_message, normalize_raw_records, step

STREAMS = ("raw-book", "raw-trades")
NORM_STREAM = {"raw-book": "norm-book", "raw-trades": "norm-trades"}
REL_DIR = {
    "raw-book": "raw/book",
    "raw-trades": "raw/trades",
    "norm-book": "norm/book",
    "norm-trades": "norm/trades",
}


def _root_size(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            total += os.path.getsize(os.path.join(dirpath, name))
    return total


class Session:
    def __init__(self, cfg):
        if getattr(cfg, "test_only", None) is not True:
            raise StartRefused("TEST_ONLY_REQUIRED")
        if getattr(cfg, "ops_approved", False):
            raise StartRefused("OPS_NOT_APPROVED")
        check_instrument(cfg.frozen_symbol, cfg.symbol)
        for name in ("prec", "freq", "length", "depth_n", "queue_cap", "disk_cap_bytes", "output_root"):
            require_set(name, getattr(cfg, name))
        if (cfg.seq_layout is None) ^ (cfg.seq_scope is None):
            raise ConfigUnset("seq_layout_and_scope")
        if cfg.seq_layout not in (None, "index_1_unverified"):
            raise StartRefused("SEQ_LAYOUT_UNKNOWN")
        if not cfg.epoch_ids:
            raise ConfigUnset("epoch_ids")
        root = Path(cfg.output_root)
        if not root.is_absolute():
            raise StartRefused("ROOT_NOT_ABSOLUTE")
        journal = root / "index.journal"
        if journal.exists():
            raise StartRefused("JOURNAL_EXISTS")
        health = root / "health.journal"
        if health.exists():
            raise StartRefused("HEALTH_JOURNAL_EXISTS")
        root.mkdir(parents=True, exist_ok=True)
        for stream in (*STREAMS, *NORM_STREAM.values()):
            confine(root, REL_DIR[stream]).mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.root = root.resolve()
        self.trace: list[str] = []
        self.index = AppendJournal(confine(self.root, "index.journal"), self.trace)
        self.health = AppendJournal(confine(self.root, "health.journal"), self.trace)
        self.files: dict[str, SegmentFile] = {}
        self.segment_id = {
            "raw-book": cfg.segment_id_book,
            "raw-trades": cfg.segment_id_trades,
            "norm-book": cfg.segment_id_norm_book,
            "norm-trades": cfg.segment_id_norm_trades,
        }
        self.member_index = {stream: 0 for stream in self.segment_id}
        self.buffer = {stream: [] for stream in STREAMS}
        self.epoch_ids = list(cfg.epoch_ids)
        self.epoch_pos = 0
        self.epoch = self.epoch_ids[0]
        self.seq_local = 0
        self.ingest_order = 0
        self.subs: dict = {}
        self.suppress_data = False
        self.received = 0
        self.attempted = 0
        self.uncommitted_received = 0
        self.durable = 0
        self.durable_raw: list[dict] = []
        self.norm_emitted = 0
        self.shadow = FeedState(prec=cfg.prec, seq_scope=cfg.seq_scope)
        self.fault = None
        self.stopped = False
        self.stderr = ""
        self.last_recv = None
        self.last_data_recv = None
        self.last_trade_recv = None
        self.last_liveness = None
        self.last_exchange = None
        self.last_trade_id = None
        self.latency_analytics_valid = True
        self.gap_count = 0
        self.last_health_ns = None
        self.header_open = {stream: False for stream in STREAMS}
        for stream in STREAMS:
            self.buffer[stream].append(self._header(stream))
            self.header_open[stream] = True

    def _stamp_common(
        self,
        channel: str,
        payload,
        verbatim,
        recv_utc_ns: int,
        recv_mono_ns: int,
        venue_seq,
        ts_exchange,
        stream: str,
        *,
        seq_missing: bool = False,
    ) -> dict:
        self.seq_local += 1
        self.ingest_order += 1
        rec = {
            "schema_v": "pmc_raw_v1",
            "venue": "bitfinex",
            "symbol": self.cfg.symbol,
            "channel": channel,
            "conn_epoch": self.epoch,
            "seq_local": self.seq_local,
            "venue_seq": venue_seq,
            "ts_exchange_ms": ts_exchange,
            "ts_recv_utc_ns": recv_utc_ns,
            "ts_recv_mono_ns": recv_mono_ns,
            "payload": payload,
            "payload_verbatim": verbatim,
            "raw_id": None,
            "segment_id": self.segment_id[stream],
            "stream": stream,
            "ingest_order": self.ingest_order,
            "seq_missing": seq_missing,
        }
        return {key: rec[key] for key in RAW_KEYS}

    def _header(self, stream: str) -> dict:
        payload = {
            "kind": "segment_header",
            "schema_v": "pmc_raw_v1",
            "venue": "bitfinex",
            "symbol": self.cfg.symbol,
            "prec": self.cfg.prec,
            "freq": self.cfg.freq,
            "len": self.cfg.length,
            "depth_n": self.cfg.depth_n,
            "subscribed_depth_not_whole_exchange_book": True,
            "aggregated_levels_are_not_order_queue": True,
            "queue_position": None,
            "public_keyless": True,
            "board_freeze": False,
            "seq_layout": self.cfg.seq_layout,
            "seq_layout_official": False,
            "stream": stream,
        }
        return self._stamp_common("info", payload, None, 0, 0, None, None, stream)

    def handle(self, message_text: str, *, recv_utc_ns: int, recv_mono_ns: int) -> None:
        if not isinstance(message_text, str):
            raise TypeError("venue message must be verbatim text")
        if self.stopped:
            raise CaptureStopped(self.stderr, self.stderr)
        self.attempted += 1
        require_set("queue_cap", self.cfg.queue_cap)
        if self.uncommitted_received >= int(self.cfg.queue_cap):
            self._stop("write_fail", "queue_full", rejected=message_text)
        parsed = json.loads(message_text)
        sub = None
        if isinstance(parsed, dict) and parsed.get("event") == "subscribed":
            if parsed.get("symbol") not in (None, self.cfg.symbol):
                raise StartRefused("INSTRUMENT_MISMATCH")
            self.subs[parsed.get("chanId")] = parsed
            sub = parsed
        elif isinstance(parsed, list) and parsed:
            sub = self.subs.get(parsed[0])
        classified = classify_message(parsed, sub, self.cfg.seq_layout)
        stream = "raw-book"
        if sub and sub.get("channel") == "trades":
            stream = "raw-trades"
        elif classified["channel"] == "trade":
            stream = "raw-trades"
        rec = self._stamp_common(
            classified["channel"],
            classified["payload"],
            message_text,
            recv_utc_ns,
            recv_mono_ns,
            classified["venue_seq"],
            classified["ts_exchange_ms"],
            stream,
            seq_missing=classified["seq_missing"],
        )
        self.buffer[stream].append(rec)
        self.uncommitted_received += 1
        self.received += 1
        self.last_recv = recv_utc_ns
        self.last_liveness = recv_utc_ns
        if classified["channel"] in {"book_snapshot", "book_delta", "trade", "checksum"}:
            self.last_data_recv = recv_utc_ns
        if classified["channel"] == "trade" and classified["ts_exchange_ms"] is not None:
            self.last_exchange = classified["ts_exchange_ms"]
            self.last_trade_recv = recv_utc_ns
        self._follow(rec)

    def _follow(self, rec: dict) -> None:
        result = step(self.shadow, rec, seq_layout=self.cfg.seq_layout)
        self.shadow = result.state
        if rec.get("channel") == "trade":
            for norm in result.norms:
                if norm.get("trade_id") is not None and norm.get("counted"):
                    self.last_trade_id = norm["trade_id"]
                    if norm.get("ts_exchange_ms") is not None:
                        self.last_exchange = norm["ts_exchange_ms"]
        if not result.followup:
            return
        payload = result.followup["payload"]
        gap = self._stamp_common(
            "gap",
            payload,
            None,
            rec["ts_recv_utc_ns"],
            rec["ts_recv_mono_ns"],
            None,
            None,
            rec["stream"],
        )
        self.buffer[rec["stream"]].append(gap)
        self.gap_count += 1
        followed = step(self.shadow, gap, seq_layout=self.cfg.seq_layout)
        self.shadow = followed.state
        if followed.followup is not None:
            raise RuntimeError("gap produced another gap")

    def observe_clock_offset(self, offset_ms: int, *, recv_utc_ns: int, recv_mono_ns: int) -> str:
        kind = classify_clock_offset(
            offset_ms, warn_ms=self.cfg.clock_warn_ms, invalid_ms=self.cfg.clock_invalid_ms
        )
        self.latency_analytics_valid = kind != "invalid"
        if kind == "ok":
            return kind
        payload = {
            "gap_start_recv": recv_utc_ns,
            "gap_end_recv": recv_utc_ns,
            "cause": "clock",
            "conn_epoch_before": self.epoch,
            "conn_epoch_after": self.epoch,
            "venue_seq_before": self.shadow.last_conn_seq,
            "venue_seq_after": self.shadow.last_conn_seq,
            "offset_ms": int(offset_ms),
            "clock_class": kind,
            "latency_analytics_valid": self.latency_analytics_valid,
        }
        gap = self._stamp_common("gap", payload, None, recv_utc_ns, recv_mono_ns, None, None, "raw-book")
        self.buffer["raw-book"].append(gap)
        self.gap_count += 1
        self._follow(gap)
        return kind

    def poll_liveness(self, now_ns: int) -> bool:
        timeout = require_set("heartbeat_timeout_s", self.cfg.heartbeat_timeout_s)
        if self.last_liveness is None:
            return False
        silent = now_ns - int(self.last_liveness)
        if silent <= float(timeout) * 1_000_000_000:
            return False
        self.disconnect(recv_utc_ns=now_ns, recv_mono_ns=now_ns, cause="disconnect")
        return True

    def plan_reconnect(self, attempt: int) -> float:
        return backoff_delay(
            attempt,
            initial_s=self.cfg.backoff_initial_s,
            cap_s=self.cfg.backoff_cap_s,
            jitter_s=self.cfg.backoff_jitter_s,
        )

    def disconnect(self, *, recv_utc_ns: int, recv_mono_ns: int, cause: str = "disconnect") -> None:
        before = self.epoch
        self.epoch_pos += 1
        if self.epoch_pos >= len(self.epoch_ids):
            raise ConfigUnset("epoch_ids")
        after = self.epoch_ids[self.epoch_pos]
        self.epoch = after
        self.seq_local = 0
        start = self.last_recv if self.last_recv is not None else recv_utc_ns
        for stream in STREAMS:
            payload = {
                "gap_start_recv": start,
                "gap_end_recv": recv_utc_ns,
                "cause": cause,
                "conn_epoch_before": before,
                "conn_epoch_after": after,
                "venue_seq_before": self.shadow.last_conn_seq,
                "venue_seq_after": None,
            }
            gap = self._stamp_common("gap", payload, None, recv_utc_ns, recv_mono_ns, None, None, stream)
            self.buffer[stream].append(gap)
            self.gap_count += 1
            self._follow(gap)
        self.last_liveness = recv_utc_ns

    def commit(self, *, from_stop: bool = False) -> None:
        if self.stopped and not from_stop:
            raise CaptureStopped(self.stderr, self.stderr)
        if from_stop and self.suppress_data:
            return
        produced: list[dict] = []
        for stream in STREAMS:
            rows = list(self.buffer[stream])
            if not rows:
                continue
            self._assign_ids(stream, rows)
            if not self._fits(rows, stream):
                if from_stop:
                    return
                self._stop("write_fail", "disk_full")
            self._write_rows(stream, rows)
            self.buffer[stream] = []
            produced.extend(rows)
        if not produced:
            return
        self.durable_raw.extend(produced)
        self.durable_raw.sort(key=lambda row: int(row["ingest_order"]))
        norms = normalize_raw_records(
            self.durable_raw,
            prec=self.cfg.prec,
            seq_scope=self.cfg.seq_scope,
            seq_layout=self.cfg.seq_layout,
        )
        fresh = norms[self.norm_emitted :]
        self.norm_emitted = len(norms)
        grouped: dict[str, list] = {stream: [] for stream in NORM_STREAM.values()}
        for norm in fresh:
            source = norm.get("stream") or "raw-book"
            grouped[NORM_STREAM.get(source, "norm-book")].append(norm)
        for stream, rows in grouped.items():
            if not rows:
                continue
            if not self._fits(rows, stream):
                if from_stop:
                    return
                self._stop("write_fail", "disk_full")
            self._write_rows(stream, rows)
        self.durable = len(self.durable_raw)
        self.uncommitted_received = 0
        self.shadow = FeedState(prec=self.cfg.prec, seq_scope=self.cfg.seq_scope)
        for row in self.durable_raw:
            self.shadow = step(self.shadow, row, seq_layout=self.cfg.seq_layout).state

    def _assign_ids(self, stream: str, rows: list[dict]) -> None:
        member = self.member_index[stream]
        segment_id = self.segment_id[stream]
        for index, row in enumerate(rows):
            row["segment_id"] = segment_id
            row["raw_id"] = f"{segment_id}:{member}:{index}"

    def _fits(self, rows: list[dict], stream: str) -> bool:
        estimate = 80 + sum(len(dumps(row)) for row in rows)
        return _root_size(self.root) + estimate <= int(self.cfg.disk_cap_bytes)

    def _segment(self, stream: str) -> SegmentFile:
        if stream not in self.files:
            relative = f"{REL_DIR[stream]}/{self.segment_id[stream]}.gz"
            self.files[stream] = SegmentFile(confine(self.root, relative), self.trace)
        return self.files[stream]

    def _write_rows(self, stream: str, rows: list[dict]) -> dict:
        segment = self._segment(stream)
        meta = {
            "stream": stream,
            "segment_id": self.segment_id[stream],
            "segment_rel": f"{REL_DIR[stream]}/{self.segment_id[stream]}.gz",
            "member_index": self.member_index[stream],
        }
        fault = self.fault if stream.startswith("raw") else None
        try:
            record = segment.commit_member(rows, self.index, label=stream, fault=fault, meta=meta)
        except OSError:
            if stream.startswith("raw"):
                self.fault = None
                self.suppress_data = True
                self._stop("write_fail", "io_error")
            raise
        self.member_index[stream] += 1
        return record

    def close_stream(self, stream: str) -> dict:
        self.commit()
        segment = self.files.get(stream)
        if segment is None:
            raise StartRefused("NOTHING_TO_CLOSE")
        blob = segment.read_bytes()
        rows, torn = decode_prefix(blob)
        if torn is not None:
            raise StartRefused("REFUSING_CLOSE_OF_TORN_SEGMENT")
        venue_seqs = [row["venue_seq"] for row in rows if row.get("venue_seq") is not None]
        recv = [row["ts_recv_utc_ns"] for row in rows]
        counts: dict[str, int] = {}
        gaps = 0
        for row in rows:
            counts[row["channel"]] = counts.get(row["channel"], 0) + 1
            if row["channel"] == "gap":
                gaps += 1
        record = {
            "kind": "close",
            "stream": stream,
            "segment_id": self.segment_id[stream],
            "segment_rel": f"{REL_DIR[stream]}/{self.segment_id[stream]}.gz",
            "segment_sha256": sha256_bytes(blob),
            "bytes": len(blob),
            "first_recv_ts": recv[0] if recv else None,
            "last_recv_ts": recv[-1] if recv else None,
            "first_venue_seq": venue_seqs[0] if venue_seqs else None,
            "last_venue_seq": venue_seqs[-1] if venue_seqs else None,
            "counts": counts,
            "gaps": gaps,
        }
        self.index.append_obj(record, label=f"{stream}:close")
        segment.mark_closed()
        return record

    def write_health(self, now_ns: int) -> dict | None:
        interval = require_set("health_interval_s", self.cfg.health_interval_s)
        if self.last_health_ns is not None and now_ns - self.last_health_ns < float(interval) * 1_000_000_000:
            return None
        event_lag = None
        if self.last_exchange is not None and self.last_data_recv is not None:
            event_lag = (self.last_data_recv / 1_000_000) - self.last_exchange
        trade_quiet = None
        if self.cfg.trade_quiet_warn_s is not None and self.last_trade_recv is not None:
            trade_quiet = (now_ns - self.last_trade_recv) > float(self.cfg.trade_quiet_warn_s) * 1_000_000_000
        record = {
            "kind": "health",
            "schema_v": "pmc_health_v1",
            "symbol": self.cfg.symbol,
            "last_venue_event_id": self.last_trade_id,
            "last_venue_seq": self.shadow.last_conn_seq,
            "last_exchange_event_time_ms": self.last_exchange,
            "last_receive_time_ns": self.last_data_recv,
            "event_lag_ms": event_lag,
            "liveness_receive_time_ns": self.last_liveness,
            "book_valid": self.shadow.valid,
            "conn_epoch": self.epoch,
            "gaps": self.gap_count,
            "durable_count": self.durable,
            "received_count": self.received,
            "free_disk_bytes": self.cfg.free_disk_bytes,
            "latency_analytics_valid": self.latency_analytics_valid,
            "trade_quiet": trade_quiet,
            "now_ns": now_ns,
        }
        self.health.append_obj(record, label="health")
        self.last_health_ns = now_ns
        return record

    def _stop(self, cause: str, detail: str, rejected: str | None = None) -> None:
        stderr = (
            f"CAPTURE_STOP cause={cause} detail={detail} "
            f"received={self.received} durable={self.durable} attempted={self.attempted}\n"
        )
        try:
            self.commit(from_stop=True)
        except CaptureStopped:
            raise
        except (OSError, ConfigUnset):
            stderr += "DATA_COMMIT_FAILED\n"
        line = {
            "kind": "capture_stop",
            "cause": cause,
            "detail": detail,
            "received": self.received,
            "durable": self.durable,
            "attempted": self.attempted,
            "rejected_counted": rejected is not None,
            "rejected_sha256": None if rejected is None else sha256_bytes(rejected.encode("utf-8")),
        }
        try:
            encoded = len(dumps(line)) + 80
            if _root_size(self.root) + encoded <= int(self.cfg.disk_cap_bytes):
                self.index.append_obj(line, label="capture_stop")
            else:
                stderr += "STOP_RECORD_OVER_CAP\n"
        except OSError:
            stderr += "JOURNAL_UNAVAILABLE\n"
        self.stopped = True
        self.stderr = stderr
        raise CaptureStopped(detail, stderr)

    def journal_records(self) -> tuple[list[dict], bool]:
        return read_journal(self.index.path)

    def health_records(self) -> tuple[list[dict], bool]:
        return read_journal(self.health.path)


def inspect_torn_tail(path: Path) -> dict:
    """Read a gzip prefix in memory. Does not write, copy, or truncate `path`."""
    before = path.read_bytes()
    before_hash = sha256_bytes(before)
    rows, torn = decode_prefix(before)
    after_hash = sha256_bytes(path.read_bytes())
    if before_hash != after_hash:
        raise RuntimeError("inspect changed the file")
    return {
        "bytes": len(before),
        "sha256": before_hash,
        "decodable_records": len(rows),
        "last_record": rows[-1] if rows else None,
        "torn_offset": torn,
        "durable": False,
        "copied": False,
        "rewritten": False,
    }


def append_startup_gap(root: Path, segment_path: Path, report: dict) -> Path:
    """Append one startup gap citation. The segment file is only read."""
    root_r = root.resolve()
    segment = segment_path.resolve()
    before = sha256_bytes(segment.read_bytes())
    journal_path = confine(root_r, "index.journal")
    if journal_path.resolve() == segment:
        raise StartRefused("JOURNAL_IS_SEGMENT")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    fd = os.open(journal_path, flags, 0o644)
    from .framing import record_line

    line = record_line(
        {
            "kind": "startup_gap",
            "cause": "startup",
            "segment": str(segment),
            "segment_sha256_observed": before,
            "decodable_records": report["decodable_records"],
            "torn_offset": report["torn_offset"],
            "last_raw_id": None if not report["last_record"] else report["last_record"].get("raw_id"),
            "durable": False,
            "lost_uncommitted_tail": True,
        }
    )
    sent = 0
    view = memoryview(line)
    while sent < len(view):
        wrote = os.write(fd, view[sent:])
        if wrote <= 0:
            raise OSError("short startup journal write")
        sent += wrote
    os.fsync(fd)
    os.close(fd)
    after = sha256_bytes(segment.read_bytes())
    if after != before:
        raise RuntimeError("startup gap rewrote the segment")
    return journal_path
