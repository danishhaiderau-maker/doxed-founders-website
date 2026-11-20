"""Frozen launch/path/environment policy. No trading imports, shell or fallback."""
from __future__ import annotations
import hashlib
import json
import ntpath
import os
import re
import stat
from datetime import datetime
from pathlib import Path

ROLES = ("pmc", "analyzer_viewer")
BASE = r"C:\DoxxedCrypto"
PYTHON = r"C:\Users\danis\AppData\Local\Programs\Python\Python311\python.exe"
PYTHON_HASH = "5f7b89a612c9b8af1d6456cdfcd1dbe5ca630849e79aebced9bee9a6694952ec"
PMC = BASE + r"\pmc\services\public-market-collector"
VIEWER = BASE + r"\v2c\services\btc-conservative-agent"
SCRIPTS = BASE + r"\v2c\scripts"
DATA = VIEWER + r"\canonical-research-data"
REPORTS = DATA + r"\analyzer"
ROOT = BASE + r"\data\collector-r2"
STATE = BASE + r"\supervisor-state"
LOGS = {"pmc": BASE + r"\data\collector-r2-logs", "analyzer_viewer": BASE + r"\v2c\logs"}
CONFIG = STATE + r"\approved-config.json"
ENTRY = SCRIPTS + r"\pmc_analyzer_supervisor.py"
SHA = re.compile(r"[0-9a-f]{64}\Z")
CONFIG_KEYS = {"schema", "installation_id", "genesis_sha256", "implementation_sha256",
               "source_sha256", "reconciled_owners_receipt_sha256", "python_sha256",
               "disk_floor_terminate_enabled", "disk_floor_card_sha256", "human_clear_receipt_sha256", "human_clear_public_key_sha256"}
REASONS = frozenset({
    "TERMINAL_EXIT_0", "TERMINAL_EXIT_75", "TERMINAL_EXIT_78", "TERMINAL_EXIT_79",
    "UNKNOWN_OR_SIGNAL_EXIT", "RESTART_BUDGET_EXHAUSTED", "STARTUP_IMPORT_FAILURE",
    "FLY_SIDE_PATH_FAILURE", "STARTUP_NOT_READY", "FOREIGN_RUNTIME_ROOT", "PREFLIGHT_REFUSED",
    "OWNERSHIP_FAILURE", "SPAWN_FAILURE", "UNCERTAIN_PREVIOUS_RUN", "CLOCK_UNCERTAIN",
    "SUPERVISOR_SHUTDOWN", "SUPERVISOR_FAILURE", "DISK_FLOOR", "JOURNAL_CAP",
    "LOG_CAP", "DURABILITY_FAILURE", "IDENTITY_MISMATCH", "GENESIS", "START_PENDING",
    "RUNNING", "EXIT_OBSERVED", "BACKOFF", "HEALTHY_RESET", "PORT_OCCUPIED", "HUMAN_CLEAR",
})

class Hold(Exception):
    """Only fixed reasons cross the diagnostic boundary; never exception text."""
    def __init__(self, reason="PREFLIGHT_REFUSED", code=78):
        self.reason = reason if reason in REASONS else "PREFLIGHT_REFUSED"
        self.code = code
        super().__init__(self.reason)

def packed(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def digest(obj):
    return hashlib.sha256(packed(obj)).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()

def no_reparse(path: Path):
    """Check every ancestor, including junctions (Python 3.11). No mkdir."""
    for item in (path, *path.parents):
        st = item.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise Hold()
    return path

def contained(child, parent):
    child, parent = ntpath.normcase(ntpath.normpath(str(child))), ntpath.normcase(ntpath.normpath(str(parent)))
    try:
        return ntpath.commonpath([child, parent]) == parent
    except ValueError:
        return False

def validate_runtime_root(path: Path):
    try:
        no_reparse(path)
        if not path.is_dir():
            raise Hold()
        with os.scandir(path) as entries:
            for i, item in enumerate(entries):
                if i >= 10000:
                    raise Hold()
                no_reparse(Path(item.path))
                if item.name in {"runtime.lock", "launch.journal", "runtime.journal"} and item.is_file(follow_symlinks=False):
                    continue
                try:
                    day = datetime.strptime(item.name, "%Y-%m-%d").strftime("%Y-%m-%d")
                except ValueError:
                    day = None
                if day != item.name or not item.is_dir(follow_symlinks=False):
                    raise Hold()
    except (Hold, OSError):
        raise Hold("FOREIGN_RUNTIME_ROOT") from None

def validate_layout():
    dirs = [PMC, VIEWER, SCRIPTS, ROOT, DATA, REPORTS, STATE, *LOGS.values()]
    for name in dirs:
        no_reparse(Path(name))
        if not Path(name).is_dir() or ntpath.normcase(str(Path(name).resolve())) != ntpath.normcase(name):
            raise Hold()
    for i, a in enumerate(dirs):
        for b in dirs[i + 1:]:
            if {a, b} in ({DATA, REPORTS}, {VIEWER, DATA}, {VIEWER, REPORTS}):
                continue  # only intentional nesting
            if contained(a, b) or contained(b, a):
                raise Hold()
    for name in (PYTHON, CONFIG, ENTRY):
        no_reparse(Path(name))

def load_config(path: Path):
    no_reparse(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise Hold()
    cfg = json.loads(path.read_bytes())
    if not isinstance(cfg, dict) or set(cfg) != CONFIG_KEYS or cfg["schema"] != 1:
        raise Hold()
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", cfg["installation_id"]):
        raise Hold()
    for key in ("genesis_sha256", "implementation_sha256", "reconciled_owners_receipt_sha256", "python_sha256"):
        if not isinstance(cfg[key], str) or not SHA.fullmatch(cfg[key]):
            raise Hold()
    if cfg["python_sha256"] != PYTHON_HASH or type(cfg["disk_floor_terminate_enabled"]) is not bool:
        raise Hold()
    for key in ("disk_floor_card_sha256", "human_clear_receipt_sha256", "human_clear_public_key_sha256"):
        if cfg[key] is not None and (not isinstance(cfg[key], str) or not SHA.fullmatch(cfg[key])):
            raise Hold()
    if cfg["human_clear_receipt_sha256"] and not cfg["human_clear_public_key_sha256"]:
        raise Hold()
    if cfg["disk_floor_terminate_enabled"] and not cfg["disk_floor_card_sha256"]:
        raise Hold()
    manifest = cfg["source_sha256"]
    if not isinstance(manifest, dict) or not manifest or len(manifest) > 10000:
        raise Hold()
    for name, sha in manifest.items():
        if not isinstance(name, str) or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise Hold()
        if (not name.endswith(".py") or ntpath.normpath(name) != name
                or not any(contained(name, root) for root in (PMC, VIEWER, SCRIPTS))):
            raise Hold()
        if contained(name, SCRIPTS) and name != ENTRY and not contained(name, SCRIPTS + r"\pmc_supervisor"):
            raise Hold()
    return cfg

def implementation_hash():
    here = Path(__file__).parent
    files = [here.parent / "pmc_analyzer_supervisor.py", *sorted(here.glob("*.py"))]
    return digest({p.name: file_hash(p) for p in files})

def source_manifest():
    files = []
    for root in (Path(PMC), Path(VIEWER), Path(SCRIPTS) / "pmc_supervisor"):
        for directory, dirs, names in os.walk(no_reparse(root), followlinks=False):
            for d in dirs:
                no_reparse(Path(directory) / d)
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "canonical-research-data"}]
            for name in names:
                if name.endswith(".py"):
                    files.append(str(no_reparse(Path(directory) / name)))
                    if len(files) > 10000:
                        raise Hold()
    files.append(ENTRY)
    return {name: file_hash(no_reparse(Path(name))) for name in sorted(files)}

def commands(role):
    if role == "pmc":
        return [PYTHON, "-B", "-m", "public_market_collector", "daily", "--root", ROOT], PMC
    if role == "analyzer_viewer":
        return [PYTHON, "-B", "-u", VIEWER + r"\research_dashboard.py", "--standalone"], VIEWER
    raise Hold()

def child_env(role, parent):
    commands(role)
    # Never carry parent values, including benign-name injection or PATH/Path duplicates.
    env = {
        "SYSTEMROOT": r"C:\Windows", "WINDIR": r"C:\Windows",
        "COMSPEC": r"C:\Windows\System32\cmd.exe", "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PATH": ntpath.dirname(PYTHON) + r";C:\Windows\System32;C:\Windows",
        "USERPROFILE": r"C:\Users\danis", "HOME": r"C:\Users\danis",
        "LOCALAPPDATA": r"C:\Users\danis\AppData\Local", "APPDATA": r"C:\Users\danis\AppData\Roaming",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8", "ARROW_DEFAULT_MEMORY_POOL": "system", "FORCE_PAPER_MODE": "1",
        "PYTHONPATH": PMC if role == "pmc" else VIEWER + ";" + VIEWER + r"\research",
    }
    if role == "analyzer_viewer":
        env.update({"BTC_AGENT_DATA_DIR": DATA, "BTC_AGENT_REPORT_DIR": REPORTS,
                    "RESEARCH_DASHBOARD_BIND_HOST": "127.0.0.1", "RESEARCH_DASHBOARD_PORT": "9001",
                    "RESEARCH_DASHBOARD_PUBLIC_URL": "http://127.0.0.1:9001",
                    "DOXXED_SELF_AWARE_URL": "http://127.0.0.1:9021",
                    "DOXXED_LAPTOP_CHAIN_STATE": BASE + r"\laptop-chain", "NO_PROXY": "127.0.0.1,localhost"})
    return env

def classify_exit(code, startup_fault=False, fly_fault=False):
    if fly_fault:
        return "FLY_SIDE_PATH_FAILURE"
    if startup_fault:
        return "STARTUP_IMPORT_FAILURE"
    if type(code) is int and code == 1:
        return None
    if type(code) is int and code in (0, 75, 78, 79):
        return "TERMINAL_EXIT_" + str(code)
    return "UNKNOWN_OR_SIGNAL_EXIT"

def exit_metadata(code):
    numeric = code if type(code) is int else None
    return {"code": numeric, "hex": None if numeric is None else f"0x{numeric & 0xffffffff:08X}",
            "classification": classify_exit(code) or "RETRY_ELIGIBLE_1"}

def backoff(attempt):
    return min(300, 6 * 2 ** min(8, max(0, attempt)))
