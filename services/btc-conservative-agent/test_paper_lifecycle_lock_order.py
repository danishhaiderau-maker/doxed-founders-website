"""Exercise the real saver without starting bot threads or external clients."""
import ast
import json
import threading
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).with_name("bot.py")


def test_trade_owner_can_save_while_background_saver_waits():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "save_paper_lifecycle")
    waiting = threading.Event()
    main_ident = threading.get_ident()

    class Lock:
        def __init__(self, notify=False):
            self.lock = threading.RLock()
            self.notify = notify

        def __enter__(self):
            if self.notify and threading.get_ident() != main_ident:
                waiting.set()
            if not self.lock.acquire(timeout=1):
                raise TimeoutError("lock order deadlock")
            return self

        def __exit__(self, *args):
            self.lock.release()

    trade = Lock(notify=True)
    file_lock = Lock()
    writes = []

    def build(reason):
        with trade:
            return {"reason": reason}

    def write(path, writer, lock, label):
        with lock:
            writes.append(label)
            return True

    namespace = dict(trade_lock=trade, paper_lifecycle_file_lock=file_lock,
                     _build_paper_lifecycle_payload=build,
                     _relay_event_outbox=SimpleNamespace(decorate_lifecycle=lambda x: x),
                     _atomic_file_replace=write, PAPER_LIFECYCLE_FILE="unused",
                     json=json, logger=SimpleNamespace(warning=lambda *a: None))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    save = namespace["save_paper_lifecycle"]
    errors = []

    def background():
        try:
            assert save("background") is True
        except BaseException as exc:
            errors.append(exc)

    with trade:
        thread = threading.Thread(target=background)
        thread.start()
        assert waiting.wait(1)
        assert save("trade_owner") is True
    thread.join(2)
    assert not thread.is_alive()
    assert errors == []
    assert len(writes) == 2


def test_all_payload_building_file_scopes_take_trade_first():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    checked = []

    def visit(node, held=()):
        if isinstance(node, ast.With):
            for item in node.items:
                name = ast.unparse(item.context_expr)
                if name == "paper_lifecycle_file_lock" and any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "_build_paper_lifecycle_payload"
                    for child in ast.walk(node)
                ):
                    assert "trade_lock" in held, node.lineno
                    checked.append(node.lineno)
                held += (name,)
        for child in ast.iter_child_nodes(node):
            visit(child, held)

    visit(tree)
    assert len(checked) == 6
