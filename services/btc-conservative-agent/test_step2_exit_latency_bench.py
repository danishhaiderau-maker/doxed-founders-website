"""Exit-latency benchmark under saturation (step 2).

Six registry-bound positions (FAMILY_DANISH_REGIME_ROUTER, hard stop 40 bp)
cross their stop at the same instant; the price keeps falling 2 bp/s.  Each
close costs CLOSE_SEC (the measured full-store verify() cost at ~20k segments,
3.2 s; the old close ran it, and it held the collector epoch lock), and
BURNERS pure-Python threads contend for the GIL like the per-second
evaluators.  BEFORE is the old path (position_manager: serial evaluation,
synchronous close, 1 s loop); AFTER is the protective exit worker (0.25 s scan,
per-position claim, close on its own thread).  Same policy, same booking rule.

    python test_step2_exit_latency_bench.py   (no pytest tests; run directly)
"""
import statistics
import threading
import time

import bot

ENTRY = 60000.0
HARD_BP = 40.0
SPEED_BP_S = 2.0
N_POS = 6
CLOSE_SEC = 3.2
BURNERS = 3


def run(mode: str) -> dict:
    stop = threading.Event()

    def burner():
        x = 0
        while not stop.is_set():
            for i in range(20000):
                x = (x * 31 + i) % 1000003

    burners = [threading.Thread(target=burner, daemon=True) for _ in range(BURNERS)]
    for t in burners:
        t.start()
    cross = time.time() + 0.5
    positions = [{
        "trade_id": f"{mode}-{i}", "research_lane": "FAMILY_DANISH_REGIME_ROUTER", "entry": ENTRY,
        "dir": "LONG", "entry_ts": cross - 60, "leverage": 100, "qty": 0.01, "status": "OPEN",
        "adaptive_entry_decision": {"regime": "QUIET"},
    } for i in range(N_POS)]
    bot.open_positions[:] = positions
    bot._protective_exit_last_eval.clear()

    def mark(*_a, **_k):
        moved = max(0.0, time.time() - cross) * SPEED_BP_S
        drop = (HARD_BP - 1.0 + moved) if time.time() >= cross else 0.0
        return ENTRY * (1 - drop / 1e4)

    results = []

    def close(pos, reason):
        results.append({"called": time.time(), "booked": pos.get("_exit_eval_price"), "reason": reason})
        time.sleep(CLOSE_SEC)
        pos["status"] = "CLOSED"

    bot.get_mark_price = mark
    bot.close_position = close
    end = cross + 60
    if mode == "before":
        while len(results) < N_POS and time.time() < end:
            now = time.time()
            for pos in [p for p in positions if p["status"] == "OPEN"]:
                bot._evaluate_position_exit(pos, mark(), now, exit_source="POSITION_MANAGER")
            time.sleep(1.0)
    else:
        while len(results) < N_POS and time.time() < end:
            bot.protective_exit_scan()
            time.sleep(bot.PROTECTIVE_EXIT_INTERVAL_SEC)
    stop.set()
    for t in burners:
        t.join()
    # crossing time of the stop itself: drop == HARD_BP at cross + 0.5 s
    trigger = cross + 1.0 / SPEED_BP_S
    lat = [r["called"] - trigger for r in results]
    over = [(-(r["booked"] - ENTRY) / ENTRY * 1e4) - HARD_BP for r in results]
    return {"mode": mode, "exits": len(results), "lat_p50_s": round(statistics.median(lat), 2),
            "lat_max_s": round(max(lat), 2), "overshoot_p50_bp": round(statistics.median(over), 2),
            "overshoot_max_bp": round(max(over), 2)}


if __name__ == "__main__":
    original = (bot.get_mark_price, bot.close_position)
    for mode in ("before", "after"):
        print(run(mode))
        time.sleep(CLOSE_SEC + 0.5)
    bot.get_mark_price, bot.close_position = original
