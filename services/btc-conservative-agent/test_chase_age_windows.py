"""5-minute signal-age chase windows: wait, hold, and TTL.

Danish: buckets are 5-minute signal-age windows, not 60s chase-count ticks.
Current toggles 2/3/4 ON, 0/1/5+ OFF. 30 min LIMIT_ORDER_MAX_AGE_SEC is the backstop.
"""
import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")


def _compile_named(names, extra=None):
    tree = ast.parse(BOT_SOURCE)
    wanted = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    }
    missing = [name for name in names if name not in wanted]
    assert not missing, f"missing functions: {missing}"
    namespace = {
        "CHASE_WINDOW_SEC": 300,
        "CHASE_WINDOW_MAX_INDEX": 5,
        "CHASE_INTRA_WINDOW_REPRICE_SEC": 180,
        "LIMIT_CHASE_INTERVAL_SEC_DEFAULT": 180,
        "LIMIT_ORDER_MAX_AGE_SEC": 1800,
        "CHASE_EXECUTION_BUCKET_ORDER": (
            "0_chases",
            "1_chase",
            "2_chases",
            "3_chases",
            "4_chases",
            "5+_chases",
        ),
    }
    if extra:
        namespace.update(extra)
    for name in names:
        exec(
            compile(ast.Module(body=[wanted[name]], type_ignores=[]), f"<{name}>", "exec"),
            namespace,
        )
    return namespace


TOGGLES_2_3_4 = {
    0: False,
    1: False,
    2: True,
    3: True,
    4: True,
    5: False,
}


def _gate_namespace():
    def chase_count_bucket(n):
        n = int(n or 0)
        if n <= 0:
            return "0_chases"
        if n == 1:
            return "1_chase"
        if n >= 5:
            return "5+_chases"
        return f"{n}_chases"

    def chase_bucket_allowed(n):
        try:
            idx = int(n or 0)
        except (TypeError, ValueError):
            idx = 0
        if idx >= 5:
            idx = 5
        return bool(TOGGLES_2_3_4.get(idx, False))

    ns = _compile_named(
        (
            "chase_age_window_index",
            "chase_window_start_sec",
            "last_enabled_chase_count",
            "min_enabled_chase_count",
            "chase_age_window_should_cancel",
            "chase_age_window_may_reprice",
            "dashboard_virtual_chase_submit_ready",
        ),
        extra={
            "Optional": __import__("typing").Optional,
            "chase_count_bucket": chase_count_bucket,
            "chase_bucket_allowed": chase_bucket_allowed,
            "get_chase_execution_buckets": lambda: {
                "0_chases": False,
                "1_chase": False,
                "2_chases": True,
                "3_chases": True,
                "4_chases": True,
                "5+_chases": False,
            },
            "_signal_age_sec": lambda signal, now=None: float(
                (signal or {}).get("_age_sec") or 0
            ),
        },
    )
    ns["chase_bucket_allowed"] = chase_bucket_allowed
    return ns


def test_windows_are_five_minutes_through_thirty():
    ns = _gate_namespace()
    idx = ns["chase_age_window_index"]
    assert idx(0) == 0
    assert idx(299) == 0
    assert idx(300) == 1
    assert idx(599) == 1
    assert idx(600) == 2
    assert idx(899) == 2
    assert idx(900) == 3
    assert idx(1199) == 3
    assert idx(1200) == 4
    assert idx(1499) == 4
    assert idx(1500) == 5
    assert idx(1799) == 5
    assert idx(1800) == 5


def test_virtual_wait_to_chase_2_takes_10_min_signal_age():
    ns = _gate_namespace()
    ready = ns["dashboard_virtual_chase_submit_ready"]
    start = ns["chase_window_start_sec"]
    assert start(2) == 600
    assert ready({"_age_sec": 0}) is False
    assert ready({"_age_sec": 540}) is False  # 9 min still waiting
    assert ready({"_age_sec": 599}) is False
    assert ready({"_age_sec": 600}) is True  # chase-2 window 10–15 min
    assert ready({"_age_sec": 899}) is True


def test_chase_2_does_not_advance_to_3_at_60s():
    ns = _gate_namespace()
    idx = ns["chase_age_window_index"]
    reprice = ns["chase_age_window_may_reprice"]
    assert idx(600) == 2
    assert idx(660) == 2  # +60s still chase 2
    assert idx(779) == 2  # +179s still chase 2
    assert idx(900) == 3
    assert reprice(660) is True
    assert reprice(900) is True


def test_early_disabled_window_pulls_resting_order_but_late_disabled_holds():
    ns = _gate_namespace()
    should_cancel = ns["chase_age_window_should_cancel"]
    reprice = ns["chase_age_window_may_reprice"]
    # 9 min: still chase-1 window. A resting order is no longer eligible and
    # must be pulled back into virtual wait until chase 2 becomes active.
    assert should_cancel(9 * 60) is True
    assert should_cancel(540) is True
    # A chase-4 rest (20–25 min) is allowed; 5+ OFF at 25–30 min holds, does not cancel.
    assert should_cancel(20 * 60) is False
    assert reprice(20 * 60) is True
    assert should_cancel(27 * 60) is False
    assert reprice(27 * 60) is False


def test_ttl_30_min_is_still_the_backstop():
    tree = ast.parse(BOT_SOURCE)
    assigns = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    assert "LIMIT_ORDER_MAX_AGE_SEC = int(os.getenv(\"LIMIT_ORDER_MAX_AGE_SEC\", str(30 * 60)))" in BOT_SOURCE
    assert "SIGNAL_TTL_SEC = int(os.getenv(\"SIGNAL_TTL_SEC\", str(30 * 60)))" in BOT_SOURCE
    assert "LIMIT_CHASE_INTERVAL_SEC_DEFAULT = 180" in BOT_SOURCE
    assert "CHASE_WINDOW_SEC = 300" in BOT_SOURCE
    assert "CHASE_INTRA_WINDOW_REPRICE_SEC = 180" in BOT_SOURCE
    assert "TTL_EXPIRED" in BOT_SOURCE
    assert "SIGNAL_TTL_EXPIRED" in BOT_SOURCE
    cleanup = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cleanup_expired_orders"
    )
    src = ast.get_source_segment(BOT_SOURCE, cleanup)
    assert "LIMIT_ORDER_MAX_AGE_SEC" in src
    assert '"TTL_EXPIRED"' in src
    # Age windows do not extend past the 30-minute lifecycle.
    ns = _gate_namespace()
    assert ns["chase_age_window_index"](30 * 60) == 5
    assert assigns  # parsed without error
