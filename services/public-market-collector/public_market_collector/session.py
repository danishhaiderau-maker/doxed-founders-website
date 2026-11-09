"""Synthetic capture session. No socket, no clock read, no second output root."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import (
    CaptureStopped,
    ConfigUnset,
    CrashFault,
    StartRefused,
    backoff_delay,
    check_instrument,
    classify_clock_offset,
    require_set,
)
from .output_policy import disk_free_bytes, foreign_entries, output_refusal
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
from .market import FeedState, classify_message, step

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


def _journal_intact(path: Path) -> None:
    _records, stopped = read_journal(path)
    if stopped:
        raise StartRefused("RESTART_TORN_JOURNAL")


class Session:
    def __init__(self, cfg):
        public = getattr(cfg, "public_capture", False) is True
        if public:
            if getattr(cfg, "test_only", False) is True:
                raise StartRefused("PUBLIC_CAPTURE_IS_NOT_A_SYNTHETIC_SESSION")
        else:
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
        resume = public and getattr(cfg, "resume", False) is True
        if public:
            reason = output_refusal(
                root,
                repo_root=getattr(cfg, "repo_root", None),
                allow_root=getattr(cfg, "allow_root", None),
                resume=resume,
            )
            if reason:
                raise StartRefused(reason)
        journal = root / "index.journal"
        health = root / "health.journal"
        exclusive = True
        if journal.exists() or health.exists():
            if not resume:
                if journal.exists():
                    raise StartRefused("JOURNAL_EXISTS")
                raise StartRefused("HEALTH_JOURNAL_EXISTS")
            if not journal.exists() or not health.exists():
                raise StartRefused("RESUME_PARTIAL_JOURNAL")
            _journal_intact(journal)
            _journal_intact(health)
            if foreign_entries(root):
                raise StartRefused("FOREIGN_OUTPUT")
            exclusive = False
        elif public and root.exists() and foreign_entries(root):
            raise StartRefused("FOREIGN_OUTPUT")
        root.mkdir(parents=True, exist_ok=True)
        for stream in (*STREAMS, *NORM_STREAM.values()):
            confine(root, REL_DIR[stream]).mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.root = root.resolve()
        self._released = False
        self.trace: list[str] = []
        self.index = AppendJournal(confine(self.root, "index.journal"), self.trace, exclusive=exclusive)
        self.health = AppendJournal(confine(self.root, "health.journal"), self.trace, exclusive=exclusive)
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
        self.keep_raw_history = not public
        self.norm_emitted = 0
        self._norm_state = FeedState(prec=cfg.prec, seq_scope=cfg.seq_scope)
        self.shadow = self._norm_state
        self.pending_resubscribe = False
        self.watch_reason = None
        self.maintenance_pause = False
        self.segment_opened_ns: dict[str, int] = {}
        self.last_clock_offset_ms = None
        self.priority_result = getattr(cfg, "priority_result", None)
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
        self.accounted_bytes = _root_size(self.root)

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
        if getattr(self.cfg, "public_capture", False) is True:
            payload["public_forward_capture"] = True
            payload["clock_source"] = "local_receive_not_ntp"
            payload["clock_skew_note"] = (
                "event_lag_ms compares host receive time with trade MTS. The host clock is not NTP-disciplined."
            )
            payload["trade_loss_note"] = (
                "Bitfinex trade ids are not contiguous. A missing id is not recorded as a lost trade."
            )
            payload["free_disk_floor_bytes"] = getattr(self.cfg, "free_disk_floor_bytes", None)
            payload["disk_watch"] = "daemon_thread_signals_writer"
            payload["launch"] = "python -B -m public_market_collector capture --output DIR --allow-root ROOT"
            applied = self.priority_result in {"posix_nice_10", "windows_below_normal"}
            payload["paper_priority"] = self.priority_result
            payload["paper_priority_applied"] = applied
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
        if (
            classified["ts_exchange_ms"] is not None
            and getattr(self.cfg, "clock_warn_ms", None) is not None
            and getattr(self.cfg, "clock_invalid_ms", None) is not None
        ):
            offset_ms = int(recv_utc_ns / 1_000_000) - int(classified["ts_exchange_ms"])
            self.observe_clock_offset(offset_ms, recv_utc_ns=recv_utc_ns, recv_mono_ns=recv_mono_ns)
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
        if result.followup and (result.followup.get("payload") or {}).get("cause") == "checksum_fail":
            self.pending_resubscribe = True
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
        self.last_clock_offset_ms = int(offset_ms)
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
            if getattr(self.cfg, "public_capture", False) is True:
                self.epoch_ids = [*self.epoch_ids, f"public-epoch-{self.epoch_pos + 1}"]
            else:
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

    def note_bad_json(self, recv_utc_ns: int, recv_mono_ns: int) -> None:
        """Keep a record of an undecodable frame. The frame is not dropped silently."""
        rec = self._stamp_common(
            "info",
            {"kind": "bad_json"},
            None,
            recv_utc_ns,
            recv_mono_ns,
            None,
            None,
            "raw-book",
        )
        self.buffer["raw-book"].append(rec)
        self.uncommitted_received += 1
        self.received += 1

    def mark_maintenance(self, recv_utc_ns: int, recv_mono_ns: int) -> None:
        payload = {
            "gap_start_recv": recv_utc_ns,
            "gap_end_recv": recv_utc_ns,
            "cause": "maintenance",
            "conn_epoch_before": self.epoch,
            "conn_epoch_after": self.epoch,
            "venue_seq_before": self.shadow.last_conn_seq,
            "venue_seq_after": self.shadow.last_conn_seq,
        }
        gap = self._stamp_common("gap", payload, None, recv_utc_ns, recv_mono_ns, None, None, "raw-book")
        self.buffer["raw-book"].append(gap)
        self.gap_count += 1
        self._follow(gap)

    def commit(self, *, from_stop: bool = False) -> None:
        if self.watch_reason and not from_stop:
            self._stop("write_fail", self.watch_reason)
        if self.stopped and not from_stop:
            raise CaptureStopped(self.stderr, self.stderr)
        if from_stop and self.suppress_data:
            return
        produced: list[dict] = []
        for stream in STREAMS:
            rows = list(self.buffer[stream])
            if not rows:
                continue
            self._prepare_write(stream, rows)
            if not self._accept_write(rows, from_stop):
                return
            self._write_rows(stream, rows)
            self.buffer[stream] = []
            produced.extend(rows)
        if not produced:
            return
        batch = sorted(produced, key=lambda row: int(row["ingest_order"]))
        if self.keep_raw_history:
            self.durable_raw.extend(batch)
        norms: list[dict] = []
        state = self._norm_state
        for rec in batch:
            result = step(state, rec, seq_layout=self.cfg.seq_layout)
            state = result.state
            norms.extend(result.norms)
        self._norm_state = state
        self.norm_emitted += len(norms)
        grouped: dict[str, list] = {stream: [] for stream in NORM_STREAM.values()}
        for norm in norms:
            source = norm.get("stream") or "raw-book"
            grouped[NORM_STREAM.get(source, "norm-book")].append(norm)
        for stream, rows in grouped.items():
            if not rows:
                continue
            self._prepare_write(stream, rows)
            if not self._accept_write(rows, from_stop):
                return
            self._write_rows(stream, rows)
        self.durable += len(batch)
        self.uncommitted_received = 0
        self.shadow = state

    def _accept_write(self, rows: list[dict], from_stop: bool) -> bool:
        if self.watch_reason:
            if from_stop:
                return False
            self._stop("write_fail", self.watch_reason)
        if not self._fits(rows, ""):
            if from_stop:
                return False
            self._stop("write_fail", "disk_full")
        blocked = self._floor_block(80 + sum(len(dumps(row)) for row in rows))
        if blocked:
            if from_stop:
                return False
            self._stop("write_fail", blocked)
        return True

    def _prepare_write(self, stream: str, rows: list[dict]) -> None:
        rolled = self._roll_if_needed(stream)
        if stream not in STREAMS:
            return
        if rolled:
            rows.insert(0, self._header(stream))
        self._assign_ids(stream, rows)

    def _roll_if_needed(self, stream: str) -> bool:
        current = self.files.get(stream)
        if current is None or current.size <= 0:
            return False
        max_bytes = getattr(self.cfg, "segment_max_bytes", None)
        max_age = getattr(self.cfg, "segment_max_age_s", None)
        roll = False
        if max_bytes is not None and current.size >= int(max_bytes):
            roll = True
        opened = self.segment_opened_ns.get(stream)
        if (
            not roll
            and max_age is not None
            and opened is not None
            and self.last_recv is not None
            and (int(self.last_recv) - int(opened)) > float(max_age) * 1_000_000_000
        ):
            roll = True
        if not roll:
            return False
        if not current.closed:
            current.mark_closed()
        self.files.pop(stream, None)
        self._advance_segment_id(stream)
        self.member_index[stream] = 0
        return True

    def _advance_segment_id(self, stream: str) -> None:
        current = self.segment_id[stream]
        head, sep, tail = current.rpartition("-")
        if sep and tail.isdigit():
            base, width, number = head, len(tail), int(tail) + 1
        else:
            base, width, number = current, 0, 2
        while number < 100000:
            nxt = f"{base}-{number:0{width}d}" if width else f"{base}-{number}"
            if not confine(self.root, f"{REL_DIR[stream]}/{nxt}.gz").exists():
                self.segment_id[stream] = nxt
                return
            number += 1
        raise StartRefused("SEGMENT_ID_EXHAUSTED")

    def _assign_ids(self, stream: str, rows: list[dict]) -> None:
        member = self.member_index[stream]
        segment_id = self.segment_id[stream]
        for index, row in enumerate(rows):
            row["segment_id"] = segment_id
            row["raw_id"] = f"{segment_id}:{member}:{index}"

    def _fits(self, rows: list[dict], stream: str) -> bool:
        estimate = 80 + sum(len(dumps(row)) for row in rows)
        return self.accounted_bytes + estimate <= int(self.cfg.disk_cap_bytes)

    def _floor_block(self, estimate: int) -> str | None:
        """Free-space floor. Independent of `_fits`, which tests are allowed to weaken."""
        floor = getattr(self.cfg, "free_disk_floor_bytes", None)
        if floor is None:
            return None
        probe = getattr(self.cfg, "disk_free_probe", None)
        if probe is None:
            free = disk_free_bytes(self.root)
        else:
            free = probe(self.root)
        if free is None:
            return "disk_free_unmeasurable"
        if int(free) - int(estimate) < int(floor):
            return "disk_floor"
        return None

    def release(self) -> None:
        """Close descriptors. Does not truncate, rename, or delete."""
        if self._released:
            return
        self._released = True
        for segment in list(self.files.values()):
            if not segment.closed:
                segment.mark_closed()
        self.index.close()
        self.health.close()

    def _segment(self, stream: str) -> SegmentFile:
        if stream not in self.files:
            relative = f"{REL_DIR[stream]}/{self.segment_id[stream]}.gz"
            self.files[stream] = SegmentFile(confine(self.root, relative), self.trace)
            self.segment_opened_ns[stream] = int(self.last_recv or 0)
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
        before = segment.size
        try:
            record = segment.commit_member(rows, self.index, label=stream, fault=fault, meta=meta)
        except CrashFault:
            self.accounted_bytes += segment.size - before
            raise
        except OSError:
            if stream.startswith("raw"):
                self.fault = None
                self.suppress_data = True
                self._stop("write_fail", "io_error")
            raise
        self.accounted_bytes += (segment.size - before) + int(segment.last_journal_bytes)
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
            "last_clock_offset_ms": self.last_clock_offset_ms,
            "clock_source": "local_receive_not_ntp",
        }
        self.accounted_bytes += self.health.append_obj(record, label="health")
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
            if self._floor_block(encoded):
                stderr += "STOP_RECORD_BELOW_FLOOR\n"
            elif self.accounted_bytes + encoded <= int(self.cfg.disk_cap_bytes):
                self.accounted_bytes += self.index.append_obj(line, label="capture_stop")
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


TAIL_READ_MAX = 256 * 1024


def _read_tail(path: Path, limit: int) -> tuple[int, int, bytes]:
    """Return (size, start, tail). Reads at most `limit` bytes."""
    size = path.stat().st_size
    start = 0 if size <= limit else size - limit
    with path.open("rb") as handle:
        handle.seek(start)
        tail = handle.read(limit)
    return size, start, tail


def inspect_torn_tail(path: Path, *, tail_max: int = TAIL_READ_MAX) -> dict:
    """Inspect a segment from its tail only. Does not write, copy, or truncate `path`."""
    limit = int(tail_max)
    size, start, tail = _read_tail(path, limit)
    digest = sha256_bytes(tail if start else tail)
    rows, torn = decode_prefix(tail)
    _size2, start2, tail2 = _read_tail(path, limit)
    if size != _size2 or start != start2 or tail != tail2:
        raise RuntimeError("inspect changed the file")
    torn_offset = None if torn is None else start + torn
    return {
        "bytes": size,
        "sha256": digest,
        "sha256_scope": "file" if start == 0 else "tail",
        "bytes_read": len(tail),
        "tail_limit": limit,
        "decodable_records": len(rows),
        "last_record": rows[-1] if rows else None,
        "torn_offset": torn_offset,
        "durable": False,
        "copied": False,
        "rewritten": False,
    }


def append_startup_gap(root: Path, segment_path: Path, report: dict) -> Path:
    """Append one startup gap citation. The segment file is only read."""
    root_r = root.resolve()
    segment = segment_path.resolve()
    limit = int(report.get("tail_limit") or TAIL_READ_MAX)
    size_before, _start_before, tail_before = _read_tail(segment, limit)
    before = report.get("sha256") or sha256_bytes(tail_before)
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
    size_after, _start_after, tail_after = _read_tail(segment, limit)
    if size_after != size_before or tail_after != tail_before:
        raise RuntimeError("startup gap rewrote the segment")
    return journal_path
