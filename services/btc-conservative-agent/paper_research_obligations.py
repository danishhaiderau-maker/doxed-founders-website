"""Durable paper research obligations; never exchange or relay instructions."""
import copy
import hashlib
import json
import math

FIELD = "paper_research_obligations"
REPLAY_FIELD = "paper_research_replay"


def _enqueue(target, row):
    if "paper_evidence_capacity" in target:
        from paper_evidence_capacity import enqueue_reserved
        updated = enqueue_reserved(target, row)
        target.update(updated)
        return row["id"]
    pending = target.setdefault(FIELD, [])
    if not any(item["id"] == row["id"] for item in pending):
        if len(pending) >= 128:
            raise RuntimeError("paper research obligation capacity exhausted")
        pending.append(row)
    return row["id"]


def enqueue_close(target, position, signal, outcome, *, epoch_id, provenance=None):
    if target.get("paper_only") is not True or target.get("live_armed") is not False:
        raise RuntimeError("research obligation requires disarmed paper state")
    trade_id = str(position.get("trade_id") or "")
    if not trade_id or not epoch_id:
        raise RuntimeError("research obligation identity missing")
    row = json.loads(json.dumps({
        "kind": "PAPER_CLOSE", "epoch_id": str(epoch_id),
        "source_revision": target.get("git_rev"),
        "provenance": dict(provenance or {}, epoch_id=str(epoch_id)),
        "position": position, "signal": signal, "outcome": outcome,
    }, sort_keys=True, allow_nan=False))
    row["id"] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return _enqueue(target, row)


def enqueue_fill(target, order, signal, position, *, epoch_id, provenance=None):
    staging = {"paper_only": target.get("paper_only"), "live_armed": target.get("live_armed"),
               "git_rev": target.get("git_rev")}
    enqueue_close(staging, position, signal, {}, epoch_id=epoch_id, provenance=provenance)
    row = staging[FIELD][0]
    row["kind"] = "PAPER_FILL"
    row["order"] = json.loads(json.dumps(order, allow_nan=False))
    del row["outcome"]
    material = {key: value for key, value in row.items() if key != "id"}
    row["id"] = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    return _enqueue(target, row)


def enqueue_cancel(target, order, signal, reason, *, epoch_id, provenance=None):
    filled = order.get("filled_qty")
    schedule = order.get("research_chase_schedule") or {}
    if (isinstance(filled, bool) or not isinstance(filled, (int, float)) or filled != 0
            or order.get("paper_fill_accounting_schema") != "paper_initial_unfilled_v1"
            or schedule.get("authoritative") is not True or schedule.get("terminal_ts") is None
            or not schedule.get("terminal_reason") or not schedule.get("intervals")
            or any(item.get("end_ts") is None for item in schedule["intervals"])):
        raise ValueError("PAPER_CANCEL_ZERO_FILL_OR_TERMINAL_PROOF_MISSING")
    requested = order.get("requested_qty", schedule.get("requested_qty", order.get("qty")))
    if isinstance(requested, bool) or not isinstance(requested, (int, float)) or not math.isfinite(requested) or requested <= 0:
        raise ValueError("PAPER_CANCEL_REQUESTED_QTY_MISSING")
    staging = {"paper_only": target.get("paper_only"), "live_armed": target.get("live_armed"), "git_rev": target.get("git_rev")}
    enqueue_close(staging, order, signal, {}, epoch_id=epoch_id, provenance=provenance)
    row = staging[FIELD][0]
    row.update(kind="PAPER_CANCEL", order=copy.deepcopy(order), reason=str(reason))
    row["order"]["requested_qty"] = requested
    del row["outcome"]
    row["id"] = hashlib.sha256(json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True).encode()).hexdigest()
    if any(item.get("id") == row["id"] for item in target.get(FIELD) or []):
        return row["id"]
    if "paper_evidence_capacity" in target:
        from paper_evidence_capacity import enqueue_cancel_reserved
        target.update(enqueue_cancel_reserved(target, row, accounting_receipt={
            "trade_id": order["trade_id"], "terminal_state": "CANCELLED", "reconciled": True, "filled_qty": 0}))
        return row["id"]
    return _enqueue(target, row)


def replay_one(outbox, file_lock, writer, *, data_dir, epoch_id, source_revision=None, fill_writer=None, cancel_writer=None):
    attempt = {}
    outcome = "RETAINED_NOT_DURABLE"
    try:
        result = _replay_one(outbox, file_lock, writer, data_dir=data_dir,
                             epoch_id=epoch_id, source_revision=source_revision,
                             fill_writer=fill_writer, cancel_writer=cancel_writer, attempt=attempt)
        outcome = "ACKNOWLEDGED" if result else outcome
        return result
    except Exception as exc:
        outcome = {ValueError: "RETAINED_VALIDATION_ERROR", OSError: "RETAINED_IO_ERROR",
                   RuntimeError: "RETAINED_RUNTIME_ERROR"}.get(type(exc), "RETAINED_WRITER_ERROR")
        raise
    finally:
        if attempt:
            with file_lock:
                latest = json.loads(outbox.path.read_text(encoding="utf-8"))
                if (not latest.get("transition_wal") and (outcome == "ACKNOWLEDGED" or any(
                        row.get("epoch_id") == str(epoch_id) for row in latest.get(FIELD) or []))):
                    state = latest.get(REPLAY_FIELD) or {}
                    changed = False
                    for item in state.get("outcomes") or []:
                        if item.get("attempt_seq") == attempt["attempt_seq"]:
                            item["outcome"] = outcome
                            changed = True
                    if changed:
                        latest[REPLAY_FIELD] = state
                        outbox._atomic_write(outbox.decorate_lifecycle(latest))


def _replay_one(outbox, file_lock, writer, *, data_dir, epoch_id, source_revision=None, fill_writer=None, cancel_writer=None, attempt):
    """At least once delivery, exact-ID writer deduplication, durable ACK last.

    Never hold the lifecycle lock during evidence IO. Re-read the latest
    generation before ACK so concurrent trading mutations remain intact.
    """
    with file_lock:
        if not outbox.healthy or not outbox.path.exists():
            return False
        current = json.loads(outbox.path.read_text(encoding="utf-8"))
        pending = current.get(FIELD) or []
        if (not pending or current.get("transition_wal") or current.get("paper_only") is not True
                or current.get("live_armed") is not False):
            return False
        earliest = {}
        for candidate in pending:
            trade = str(candidate.get("position", {}).get("trade_id") or candidate.get("id") or "UNKNOWN")
            earliest.setdefault(trade, candidate)
        earliest = {trade: candidate for trade, candidate in earliest.items()
                    if candidate.get("epoch_id") == str(epoch_id)
                    and (candidate.get("provenance", {}).get("source_revision")
                         or candidate.get("source_revision") == source_revision)}
        if not earliest:
            return False
        trades = list(earliest)
        state = current.get(REPLAY_FIELD) or {}
        previous = state.get("cursor_trade_id")
        index = (trades.index(previous) + 1) % len(trades) if previous in trades else 0
        trade = trades[index]
        row = copy.deepcopy(earliest[trade])
        sequence = int(state.get("attempt_seq") or 0) + 1
        entry = {"attempt_seq": sequence, "trade_id": trade, "kind": row.get("kind"), "outcome": "STARTED"}
        current[REPLAY_FIELD] = {"cursor_trade_id": trade, "attempt_seq": sequence,
                                 "outcomes": [*(state.get("outcomes") or [])[-31:], entry]}
        outbox._atomic_write(outbox.decorate_lifecycle(current))
        attempt.update(entry)
        material = {key: value for key, value in row.items() if key != "id"}
        if row.get("id") != hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest():
            raise ValueError("PAPER_RESEARCH_OBLIGATION_HASH_MISMATCH")
        if (current.get("paper_only") is not True or current.get("live_armed") is not False
                or row.get("epoch_id") != str(epoch_id) or row.get("kind") not in ("PAPER_CLOSE", "PAPER_FILL", "PAPER_CANCEL")):
            return False
        if not row.get("provenance", {}).get("source_revision"):
            # Legacy obligations without frozen provenance remain retained.
            if row.get("source_revision") != source_revision:
                return False
    kwargs = {}
    if row.get("provenance", {}).get("source_revision"):
        kwargs["recovery_provenance"] = row["provenance"]
    if row["kind"] == "PAPER_CLOSE":
        if "write_plan" not in row and getattr(writer, "supports_durable_write_plan", False):
            plan = writer(row["position"], row["signal"], row["outcome"],
                          epoch_id=row["epoch_id"], data_dir=data_dir, prepare_only=True, **kwargs)
            encoded = json.dumps(plan, sort_keys=True, allow_nan=False)
            if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
                raise ValueError("PAPER_CLOSE_WRITE_PLAN_OVERSIZE")
            prepared = copy.deepcopy(row)
            prepared["write_plan"] = json.loads(encoded)
            prepared["id"] = hashlib.sha256(json.dumps(
                {key: value for key, value in prepared.items() if key != "id"}, sort_keys=True).encode()).hexdigest()
            with file_lock:
                latest = json.loads(outbox.path.read_text(encoding="utf-8"))
                if (latest.get("transition_wal") or latest.get("paper_only") is not True
                        or latest.get("live_armed") is not False or row not in (latest.get(FIELD) or [])):
                    return False
                if "paper_evidence_capacity" in latest:
                    from paper_evidence_capacity import replace_prepared
                    latest = replace_prepared(latest, row, prepared)
                else:
                    latest[FIELD] = [prepared if item == row else item for item in latest[FIELD]]
                outbox._atomic_write(outbox.decorate_lifecycle(latest))
            row = prepared
        if "write_plan" in row:
            kwargs["write_plan"] = row["write_plan"]
    if row["kind"] == "PAPER_CANCEL":
        if cancel_writer is None:
            return False
        receipt = cancel_writer(row["order"], row["signal"], row["reason"],
                                epoch_id=row["epoch_id"], data_dir=data_dir, **kwargs)
    elif row["kind"] == "PAPER_FILL":
        if fill_writer is None:
            return False
        receipt = fill_writer(row["order"], row["signal"], row["position"],
                              epoch_id=row["epoch_id"], data_dir=data_dir, **kwargs)
    else:
        receipt = writer(row["position"], row["signal"], row["outcome"],
                         epoch_id=row["epoch_id"], data_dir=data_dir, **kwargs)
    writes = receipt.get("writes") or []
    trade_id = row["position"]["trade_id"]
    keys = [(item.get("ledger"), item.get("record_id")) for item in writes if isinstance(item, dict)]
    required = {("execution", f"execution:{trade_id}:paper-close"),
                ("lifecycle", f"lifecycle:{trade_id}:paper-closed")}
    if row["kind"] == "PAPER_FILL":
        required = {("execution", f"execution:{trade_id}:primary-fill"),
                    ("lifecycle", f"lifecycle:{trade_id}:paper-filled")}
    elif row["kind"] == "PAPER_CANCEL":
        required = {("execution", f"execution:{trade_id}:paper-cancel"),
                    ("lifecycle", f"lifecycle:{trade_id}:paper-cancelled")}
    if not required.issubset(keys) or len(keys) != len(set(keys)):
        return False
    if not writes or not all(
        isinstance(item, dict) and not item.get("blocked") and not item.get("deferred")
        and (item.get("written") is True or item.get("duplicate") is True)
        for item in writes
    ):
        return False
    with file_lock:
        current = json.loads(outbox.path.read_text(encoding="utf-8"))
        if (current.get("transition_wal") or current.get("paper_only") is not True
                or current.get("live_armed") is not False
                or row not in (current.get(FIELD) or [])):
            return False
        if "paper_evidence_capacity" in current:
            from paper_evidence_capacity import acknowledge
            current = acknowledge(current, trade_id, row["kind"], row["id"])
        else:
            current[FIELD] = [item for item in current.get(FIELD) or [] if item != row]
        outbox._atomic_write(outbox.decorate_lifecycle(current))
    return True
