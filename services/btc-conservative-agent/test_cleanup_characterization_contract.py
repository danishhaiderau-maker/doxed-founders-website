import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
SOURCE = ROOT / "bot.py"
MANIFEST = ROOT / "cleanup_characterization_contract.fixture"


def _tree():
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _literal(node):
    return ast.literal_eval(node)


def test_route_manifest_freezes_every_flask_entrypoint():
    routes = []
    for node in _tree().body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.func.attr in {"route", "get", "post", "put", "delete", "patch"}
            ):
                continue
            path = _literal(decorator.args[0])
            methods = None
            for keyword in decorator.keywords:
                if keyword.arg == "methods":
                    methods = _literal(keyword.value)
            if methods is None:
                methods = [decorator.func.attr.upper()] if decorator.func.attr != "route" else ["GET"]
            routes.extend([method.upper(), path, node.name] for method in methods)
    assert sorted(routes) == sorted(_manifest()["routes"])


def test_main_worker_manifest_freezes_background_ownership():
    main = next(node for node in _tree().body if isinstance(node, ast.FunctionDef) and node.name == "main")
    workers = []
    for node in ast.walk(main):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "threading" and node.func.attr == "Thread"):
            continue
        target = next((kw.value for kw in node.keywords if kw.arg == "target"), None)
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Name) and target.func.id == "safe_thread":
            target = target.args[0]
        if isinstance(target, ast.Name):
            workers.append(target.id)
    assert sorted(workers) == sorted(_manifest()["main_workers"])


def test_main_worker_manifest_freezes_wrapper_and_daemon_contract():
    main = next(node for node in _tree().body if isinstance(node, ast.FunctionDef) and node.name == "main")
    workers = []
    for node in ast.walk(main):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "threading"
            and node.func.attr == "Thread"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        target = keywords.get("target")
        wrapped = False
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Name) and target.func.id == "safe_thread":
            wrapped = True
            target = target.args[0]
        if not isinstance(target, ast.Name):
            continue
        workers.append({
            "target": target.id,
            "safe_thread": wrapped,
            "daemon": _literal(keywords["daemon"]) if "daemon" in keywords else None,
        })
    assert sorted(workers, key=lambda row: row["target"]) == sorted(
        _manifest()["worker_start_contract"], key=lambda row: row["target"]
    )


def test_import_receipt_has_no_unguarded_main_or_thread_start():
    tree = _tree()
    main_guards = [
        node for node in tree.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
    ]
    assert len(main_guards) == 1
    assert [ast.unparse(node) for node in main_guards[0].body] == [
        "_require_fly_runtime_for_direct_start()",
        "main()",
    ]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or node in main_guards:
            continue
        assert not any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "start"
            for child in ast.walk(node)
        ), f"module import starts a worker at line {getattr(node, 'lineno', '?')}"


def test_import_side_effect_receipt_freezes_known_resource_creation_and_mutation():
    """Make import-time behavior visible before code is split into modules.

    This is a characterization receipt, not an endorsement: future cleanup may
    deliberately reduce the allow-list and update the fixture in the same
    reviewed change, but cannot silently introduce a new resource or mutation.
    """
    effect_names = {
        "_load_spread_gate_config", "_load_local_dotenv", "_load_markets_with_retry",
        "SafeRotatingFileHandler", "Flask", "Queue",
    }
    effect_calls = {"requests.Session", "ccxt.bitfinex", "logging.StreamHandler", "signal.signal"}
    mutating_methods = {"update", "mount", "setLevel", "setFormatter", "clear", "addHandler"}
    effects = []
    for top_level in _tree().body:
        if isinstance(top_level, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        for call in ast.walk(top_level):
            if not isinstance(call, ast.Call):
                continue
            rendered = ast.unparse(call.func)
            if (
                (isinstance(call.func, ast.Name) and call.func.id in effect_names)
                or rendered in effect_calls
                or (isinstance(call.func, ast.Attribute) and call.func.attr in mutating_methods)
            ):
                effects.append(rendered)
    assert effects == _manifest()["import_side_effect_calls"]


def test_critical_state_schema_keys_remain_initialized():
    state_assignment = next(
        node for node in _tree().body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "state" for target in node.targets)
    )
    assert isinstance(state_assignment.value, ast.Dict)
    initialized = {_literal(key) for key in state_assignment.value.keys if key is not None}
    assert set(_manifest()["critical_state_keys"]) <= initialized


def test_full_state_schema_sequence_and_duplicate_keys_are_frozen():
    state_assignment = next(
        node for node in _tree().body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "state" for target in node.targets)
    )
    keys = [_literal(key) for key in state_assignment.value.keys if key is not None]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    digest = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()
    schema = _manifest()["state_schema"]
    assert len(keys) == schema["key_count"]
    assert digest == schema["ordered_key_sha256"]
    assert duplicates == schema["duplicate_keys"]


class _NestedLockVisitor(ast.NodeVisitor):
    def __init__(self):
        self.function = "<module>"
        self.stack = []
        self.pairs = set()

    def visit_FunctionDef(self, node):
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_With(self, node):
        locks = []
        for item in node.items:
            expr = item.context_expr
            if isinstance(expr, ast.Name) and (expr.id.endswith("_lock") or expr.id in {"state_lock", "trade_lock", "replay_lock", "file_lock"}):
                locks.append(expr.id)
            elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "get_lane_lock":
                locks.append("lane_lock")
        for lock in locks:
            for outer in self.stack:
                if outer != lock:
                    self.pairs.add((self.function, outer, lock))
        self.stack.extend(locks)
        for child in node.body:
            self.visit(child)
        if locks:
            del self.stack[-len(locks):]


def test_nested_lock_order_has_no_new_inversions():
    visitor = _NestedLockVisitor()
    visitor.visit(_tree())
    expected = {tuple(row) for row in _manifest()["known_nested_lock_exception"]}
    assert visitor.pairs == expected


def test_historical_ui_candidates_are_inventory_only():
    candidates = _manifest()["historical_ui_tile_candidates"]
    assert candidates
    assert all(row["action"] in {"inventory-only", "preserve-evidence"} for row in candidates)
    source = SOURCE.read_text(encoding="utf-8")
    for label in ("Edge Acceleration", "Profit Gates", "tile2_counters"):
        assert label in source


def test_retired_research_controls_are_read_only_and_truthfully_labelled():
    functions = {
        node.name: node
        for node in _tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    profit_toggle = ast.unparse(functions["toggle_profit_gates"])
    tile_reset = ast.unparse(functions["api_tile2_reset_counters"])
    tile_metrics = ast.unparse(functions["api_tile2_metrics"])
    assert "RETIRED_RESEARCH_CONTROL" in profit_toggle
    assert "save_persistent_config" not in profit_toggle
    assert "state[" not in profit_toggle
    assert "RETIRED_RESEARCH_CONTROL" in tile_reset
    assert "reset_tile2_counters_for_fresh_holdout" not in tile_reset
    assert "RETIRED_HISTORICAL" in tile_metrics
    assert "mutable" in tile_metrics
    source = SOURCE.read_text(encoding="utf-8")
    assert "safeText('tile2Metrics'" not in source
    assert "safeText('tile2DecisionSummary'" not in source


def test_proven_dead_compatibility_functions_stay_removed():
    names = {
        node.name
        for node in _tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert names.isdisjoint({
        "_spawn_research_lane",
        "maybe_tick_a160_v2_research",
        "spawn_research_lanes_from_continuous",
        "spawn_profit_gates_lane",
        "should_call_ai",
        "ai_loop",
    })


def test_retired_one_shot_patchers_and_competing_health_server_stay_removed():
    scripts = REPO_ROOT / "scripts"
    retired = (
        "fix-integrity-ai-funnel.py",
        "implement-analyzer-vnext-phase1.py",
        "patch-research-dashboard-20260705.py",
        "patch-empty-sections-dashboard-20260705.py",
        "analyzer-health-server.py",
    )
    assert all(not (scripts / name).exists() for name in retired)
