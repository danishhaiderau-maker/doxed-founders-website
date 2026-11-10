"""Two-role state machine. Only actual rc=1 can reserve a bounded restart."""
from __future__ import annotations
import copy
import os
import signal
import threading
import time
from pathlib import Path
from . import policy as P
from .health import Cache, CollectorSample, DiskSample, HealthServer, disk_free, probe_viewer, reserve_port, GIB
from .journal import InstanceLock, Journal
from .win32 import API, OwnedProcess


class Supervisor:
    """Dependency seams are test-only Python constructors, not config/CLI hooks."""
    def __init__(self, journal, spawn, preflight, *, wall=time.time, mono=time.monotonic,
                 free=disk_free, probe=probe_viewer):
        self.journal, self.spawn, self.preflight = journal, spawn, preflight
        self.wall, self.mono, self.free, self.probe = wall, mono, free, probe
        self.processes, self.deadlines, self.started, self.healthy_since, self.disk_stops = {}, {}, {}, {}, {}
        self.cache = Cache()
        self.viewer_sample = {"alive": False, "ready": False}
        self.collector_sample = {"status": "UNKNOWN"}
        self.disk_sample = {"free_bytes": None, "status": "UNKNOWN"}
        self.sample_mono, self.sample_wall = None, None
        self.started_mono = mono()
        self.stop_event = threading.Event()
        self.failure = None
        self.ownership = {}
        self.pending_wall = {}
        self.anchor_wall, self.anchor_mono = wall(), mono()
        self.reservation_clocks = {r: [(t, self.anchor_mono - (self.anchor_wall - t))
                                      for t in journal.state[r]["reservations"]] for r in P.ROLES}

    def transition(self, role, reason, **changes):
        state = copy.deepcopy(self.journal.state)
        state[role].update(changes)
        self.journal.write(role, reason, state)

    def latch(self, role, reason):
        self.journal.latch(role, reason)

    def active_reservations(self, role):
        # Monotonic within an invocation: a wall-clock step must not free slots.
        return [(wall, mono) for wall, mono in self.reservation_clocks[role] if mono > self.mono() - 1800]

    def clock_uncertain(self):
        return abs((self.wall() - self.anchor_wall) - (self.mono() - self.anchor_mono)) > 5

    def start(self, role, restart=False):
        row = self.journal.state[role]
        if self.journal.just_cleared:
            return  # clear and start are distinct authorized operations/invocations
        if row["phase"] == "LATCHED":
            return
        now = self.wall()
        if now < self.journal.last["wall"] or self.clock_uncertain():
            self.latch(role, "CLOCK_UNCERTAIN")
            return
        reservations = [t for t, _ in self.active_reservations(role)]
        if any(t > now for t in reservations):
            self.latch(role, "CLOCK_UNCERTAIN")
            return
        if restart and len(reservations) >= 3:
            self.latch(role, "RESTART_BUDGET_EXHAUSTED")
            return
        try:
            self.preflight(role)
            free = self.free()
            if free is None or (role == "pmc" and free < 20 * GIB):
                raise P.Hold("DISK_FLOOR")
        except P.Hold as exc:
            self.latch(role, exc.reason)
            return
        except Exception:
            self.latch(role, "PREFLIGHT_REFUSED")
            return
        # Preflight can be slow. Count at the durable reservation time, not the
        # earlier hash/disk check time; otherwise slow preflights free budget early.
        reserved_at = self.wall()
        if reserved_at < now or reserved_at < self.journal.last["wall"] or self.clock_uncertain():
            self.latch(role, "CLOCK_UNCERTAIN")
            return
        active = self.active_reservations(role)
        reservations = [t for t, _ in active]
        if any(t > reserved_at for t in reservations):
            self.latch(role, "CLOCK_UNCERTAIN")
            return
        if restart and len(reservations) >= 3:
            self.latch(role, "RESTART_BUDGET_EXHAUSTED")
            return
        if restart:
            reservations.append(reserved_at)
        # Reservation and pending intent are one durable record BEFORE process creation.
        self.transition(role, "START_PENDING", phase="START_PENDING", reservations=reservations,
                        attempt=row["attempt"] + (1 if restart else 0), deadline=None, identity=None)
        self.reservation_clocks[role] = active + ([(reserved_at, self.mono())] if restart else [])
        self.pending_wall[role] = reserved_at
        try:
            proc = self.spawn(role)
            self.processes[role] = proc
            if not proc.owned():
                raise P.Hold("OWNERSHIP_FAILURE")
            self.ownership[role] = True
            self.transition(role, "RUNNING", phase="RUNNING", identity=proc.identity)
            self.started[role] = self.mono()
            self.healthy_since.pop(role, None)
        except P.Hold as exc:
            self.latch(role, exc.reason)
        except Exception:
            self.latch(role, "SPAWN_FAILURE")

    def exit(self, role, code, flags):
        row = self.journal.state[role]
        # Preserve a preexisting terminal latch (including Fly/startup/disk).
        already = row["phase"] == "LATCHED"
        self.transition(role, "EXIT_OBSERVED", phase="LATCHED" if already else "EXIT_OBSERVED",
                        exit=P.exit_metadata(code))
        if already:
            return
        reason = P.classify_exit(code, "STARTUP_IMPORT_FAILURE" in flags, "FLY_SIDE_PATH_FAILURE" in flags)
        if "DURABILITY_FAILURE" in flags and reason != "FLY_SIDE_PATH_FAILURE":
            reason = "DURABILITY_FAILURE"
        if "LOG_CAP" in flags and reason is None:
            reason = "LOG_CAP"
        if reason:
            self.latch(role, "FOREIGN_RUNTIME_ROOT" if code == 78 and "FOREIGN_RUNTIME_ROOT" in flags else reason)
            return
        now, row = self.wall(), self.journal.state[role]
        if any(t > now for t in row["reservations"]) or now < self.journal.last["wall"] or self.clock_uncertain():
            self.latch(role, "CLOCK_UNCERTAIN")
        elif len(self.active_reservations(role)) >= 3:
            self.latch(role, "RESTART_BUDGET_EXHAUSTED")
        else:
            delay = P.backoff(row["attempt"])
            self.transition(role, "BACKOFF", phase="BACKOFF", deadline=now + delay)
            self.deadlines[role] = self.mono() + delay

    def ready(self, role):
        if role not in self.processes or self.sample_mono is None or self.sample_mono < self.started.get(role, float("inf")):
            return False
        if role == "analyzer_viewer":
            return bool(self.viewer_sample.get("ready") and self.viewer_sample.get("expected_pid") == self.processes[role].identity["pid"])
        receipt = self.collector_sample.get("runtime") or {}
        start = receipt.get("run_start_ns")
        return bool(self.collector_sample.get("status") == "GREEN" and type(start) is int and start / 1e9 >= self.pending_wall.get(role, float("inf")))

    def tick(self):
        now, mono = self.wall(), self.mono()
        if now < self.journal.last["wall"] or self.clock_uncertain():
            # In-memory HOLD plus durable old unresolved intent; never award a reset.
            self.failure = "CLOCK_UNCERTAIN"
            self.stop_event.set()
            return
        for role in P.ROLES:
            proc = self.processes.get(role)
            if proc is not None:
                flags = set(proc.flags)
                for reason in ("FLY_SIDE_PATH_FAILURE", "STARTUP_IMPORT_FAILURE", "DURABILITY_FAILURE", "LOG_CAP"):
                    if reason in flags:
                        self.latch(role, reason)
                        break
                self.ownership[role] = proc.owned()
                if not self.ownership[role]:
                    self.latch(role, "OWNERSHIP_FAILURE")
                    continue  # never kill/adopt an identity that fails ownership
                ended, code = proc.poll()
                if ended:
                    self.exit(role, code, set(proc.flags))
                    proc.close()
                    del self.processes[role]
                    self.healthy_since.pop(role, None)
                    continue
                if (role == "analyzer_viewer" and self.journal.state[role]["phase"] == "RUNNING"
                        and mono - self.started[role] >= 120 and not self.ready(role)):
                    self.latch(role, "STARTUP_NOT_READY")  # do not stop/relaunch the still-live viewer
                if self.viewer_sample.get("fly_failure") and role == "analyzer_viewer":
                    self.latch(role, "FLY_SIDE_PATH_FAILURE")
                fresh = self.sample_mono is not None and 0 <= mono - self.sample_mono <= 30
                good = self.ready(role)
                if fresh and good and self.journal.state[role]["phase"] == "RUNNING":
                    since = self.healthy_since.setdefault(role, mono)
                    if mono - since >= 1800 and self.journal.state[role]["attempt"] > 0:
                        self.transition(role, "HEALTHY_RESET", attempt=0)
                else:
                    self.healthy_since.pop(role, None)
                if role == "pmc" and fresh:
                    free = self.disk_sample.get("free_bytes")
                    if free is not None and free < 20 * GIB:
                        self.latch(role, "DISK_FLOOR")
                        # RK bb1b36ebe306: default DISABLED; enabling needs a separate card.
                        if self.journal.cfg.get("disk_floor_terminate_enabled", False) and role not in self.disk_stops:
                            proc.graceful_stop()
                            self.disk_stops[role] = {"requested": mono, "terminated": False}
                    if role in self.disk_stops and not self.disk_stops[role]["terminated"] and mono - self.disk_stops[role]["requested"] >= 10:
                        proc.terminate()  # actual exit is subsequently observed, never forged as 75
                        self.disk_stops[role]["terminated"] = True
                continue
            row = self.journal.state[role]
            if row["phase"] == "NEVER_STARTED":
                self.start(role)
            elif row["phase"] == "BACKOFF" and mono >= self.deadlines.get(role, float("inf")):
                self.start(role, restart=True)
        # Cache publication never waits on file scanning/HTTP. Snapshot includes all roles.
        self.publish()

    def publish(self):
        roles = {}
        for role in P.ROLES:
            row = copy.deepcopy(self.journal.state[role])
            row["alive"] = role in self.processes
            row["owned"] = role in self.processes and self.ownership.get(role, False)
            row["restart_window_count"] = len(self.active_reservations(role))
            row["restart_budget"] = 3
            row["ready"] = self.ready(role)
            roles[role] = row
        latched = any(row["phase"] == "LATCHED" for row in roles.values())
        ready = all(row["phase"] == "RUNNING" and row["ready"] for row in roles.values())
        stale = self.sample_mono is None or self.mono() - self.sample_mono > 30
        status = "RED/HOLD" if latched or self.failure else "UNKNOWN" if stale else "GREEN" if ready and self.disk_sample.get("status") == "GREEN" else "AMBER/HOLD"
        snapshot = {"status": status, "live_trading": "OFF", "required_roles": list(P.ROLES),
                    "supervisor": {"pid": os.getpid(), "age_s": self.mono() - self.started_mono,
                                   "run_id": self.journal.run_id, "code_hash": self.journal.code_hash,
                                   "config_hash": self.journal.config_hash, "failure": self.failure},
                    "sampled_at": self.sample_wall,
                    "roles": roles, "pmc": self.collector_sample, "viewer": self.viewer_sample, "disk": self.disk_sample,
                    "holds": ["BROWSER_FLY_SIDE_PATH_UNRESOLVED", "RESEARCH_COMPUTE_NOT_SUPPLIED"],
                    "disk_floor_termination_enabled": self.journal.cfg.get("disk_floor_terminate_enabled", False)}
        self.cache.publish(snapshot, self.sample_mono)

    def sample_loop(self):
        collector, disk = CollectorSample(), DiskSample()
        while not self.stop_event.is_set():
            try:
                now = self.wall()
                pmc = collector.sample(now)
                proc = self.processes.get("analyzer_viewer")
                viewer = self.probe(proc.identity["pid"]) if proc else {"alive": False, "ready": False}
                free = disk.sample(now, self.free())
                self.collector_sample, self.viewer_sample, self.disk_sample = pmc, viewer, free
                self.sample_wall, self.sample_mono = now, self.mono()
            except Exception:
                pass  # stale sample becomes UNKNOWN, never triggers a restart
            self.stop_event.wait(10)

    def shutdown(self):
        # All roles latch BEFORE any owned process handles are released.
        for role in P.ROLES:
            self.latch(role, self.failure or "SUPERVISOR_SHUTDOWN")
        for proc in self.processes.values():
            if proc.owned():
                proc.graceful_stop()
        deadline = self.mono() + 10
        while self.processes and self.mono() < deadline:
            for role, proc in list(self.processes.items()):
                ended, code = proc.poll()
                if ended:
                    self.exit(role, code, set(proc.flags))
                    proc.close()
                    del self.processes[role]
            if self.processes:
                time.sleep(0.1)
        # Job close is the reviewed owned-process crash/shutdown containment path,
        # distinct from the DISABLED running disk-floor termination feature.
        for proc in self.processes.values():
            proc.close()  # unresolved actual exit remains uncertainty in the latch
        self.processes.clear()


def run(config_path):
    if os.name != "nt" or str(config_path) != P.CONFIG:
        raise P.Hold()
    P.validate_layout()
    lock = InstanceLock(Path(P.STATE) / "supervisor.lock")  # BEFORE any state read
    journal, server, supervisor, api = None, None, None, None
    pins, previous_signals = [], {}
    console_callback = None
    try:
        cfg = P.load_config(Path(config_path))
        api = API()
        pins.append(api.pin_file(config_path))
        if cfg != P.load_config(Path(config_path)):
            raise P.Hold("IDENTITY_MISMATCH")
        for path in (P.PMC, P.VIEWER, P.SCRIPTS, P.ROOT, P.DATA, P.REPORTS, P.STATE, *P.LOGS.values()):
            pins.append(api.pin_directory(path))
        for path in (*cfg["source_sha256"], P.PYTHON):
            pins.append(api.pin_file(path))
        if (P.implementation_hash() != cfg["implementation_sha256"] or
                P.source_manifest() != cfg["source_sha256"] or P.file_hash(P.PYTHON) != cfg["python_sha256"]):
            raise P.Hold("IDENTITY_MISMATCH")
        receipt = P.no_reparse(Path(P.STATE) / "owners-reconciled.json")
        pins.append(api.pin_file(receipt))
        if P.file_hash(receipt) != cfg["reconciled_owners_receipt_sha256"]:
            raise P.Hold("OWNERSHIP_FAILURE")
        if cfg["disk_floor_terminate_enabled"]:
            card = P.no_reparse(Path(P.STATE) / "disk-floor-approved-card.json")
            pins.append(api.pin_file(card))
            if P.file_hash(card) != cfg["disk_floor_card_sha256"]:
                raise P.Hold("IDENTITY_MISMATCH")
        journal = Journal(Path(P.STATE) / "supervisor-state.jsonl", cfg)
        if journal.just_cleared:
            return 78  # durable HUMAN_CLEAR only; no start in the recovery invocation
        viewer_port = None
        # Occupancy is a per-role preflight refusal; health remains up for latches.
        def preflight(role):
            P.commands(role)
            P.validate_layout()
            if P.implementation_hash() != cfg["implementation_sha256"] or P.source_manifest() != cfg["source_sha256"]:
                raise P.Hold("IDENTITY_MISMATCH")
            for name in ("stdout", "stderr"):
                path = P.no_reparse(Path(P.LOGS[role]) / ("supervisor-" + role + "-" + name + ".log"))
                if not path.is_file() or path.stat().st_size >= 8 * 1024 * 1024:
                    raise P.Hold("LOG_CAP")
            if role == "pmc":
                P.validate_runtime_root(Path(P.ROOT))
            else:
                nonlocal viewer_port
                viewer_port = reserve_port(9001)  # held until immediately before spawn
        def spawn(role):
            nonlocal viewer_port
            argv, cwd = P.commands(role)
            if viewer_port:
                viewer_port.close()
                viewer_port = None
            return OwnedProcess(api, argv, cwd, P.child_env(role, os.environ), journal.config_hash,
                                Path(P.LOGS[role]) / ("supervisor-" + role + "-stdout.log"),
                                Path(P.LOGS[role]) / ("supervisor-" + role + "-stderr.log"))
        supervisor = Supervisor(journal, spawn, preflight)
        server = HealthServer(supervisor.cache)  # only one in-process listener
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            previous_signals[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: supervisor.stop_event.set())
        # Console close/logoff/shutdown callback lifetime is held until unregister.
        import ctypes as C
        CALLBACK = C.WINFUNCTYPE(C.c_int32, C.c_uint32)
        def console_event(event):
            if event in (0, 1, 2, 5, 6):
                supervisor.stop_event.set()
                # Return promptly. OS timeout/kill => unresolved state latches on replay.
                return 1
            return 0
        console_callback = CALLBACK(console_event)
        api.checked("SetConsoleCtrlHandler", C.cast(console_callback, C.c_void_p), True)
        threading.Thread(target=supervisor.sample_loop, daemon=True).start()
        try:
            while not supervisor.stop_event.is_set():
                supervisor.tick()
                supervisor.stop_event.wait(0.2)
        except P.Hold as exc:
            supervisor.failure = exc.reason
        except Exception:
            supervisor.failure = "SUPERVISOR_FAILURE"
        finally:
            supervisor.stop_event.set()
            supervisor.shutdown()
        return 78
    finally:
        if server:
            server.shutdown()
            server.server_close()
        for sig, handler in previous_signals.items():
            signal.signal(sig, handler)
        if api and console_callback:
            import ctypes as C
            api.call("SetConsoleCtrlHandler", C.cast(console_callback, C.c_void_p), False)
        # Even a journal-fsync failure cannot leave owned children detached.
        if supervisor:
            for proc in supervisor.processes.values():
                proc.close()
        if journal:
            journal.close()
        for h in pins:
            api.close(h)
        lock.close()
