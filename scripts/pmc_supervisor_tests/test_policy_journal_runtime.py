"""Socket-free deterministic safety, crash, lifecycle and boundary regressions."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pmc_supervisor import policy as P
from pmc_supervisor.journal import Journal, InstanceLock, genesis_record, packed, config_identity, RESERVE
from pmc_supervisor.runtime import Supervisor
from pmc_supervisor.health import GIB

class Clock:
    def __init__(self): self.t, self.m = 1800000000.0, 100.0
    def wall(self): return self.t
    def mono(self): return self.m
    def advance(self, seconds): self.t += seconds; self.m += seconds

@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    cfg = {"schema": 1, "installation_id": "synthetic-test-install", "implementation_sha256": "a" * 64,
           "genesis_sha256": "", "disk_floor_terminate_enabled": False, "human_clear_receipt_sha256": None,
           "human_clear_public_key_sha256": None}
    record = genesis_record(cfg, clock.wall(), "synthetic-genesis")
    cfg["genesis_sha256"] = record["hash"]
    path = tmp_path / "supervisor-state.jsonl"
    path.write_bytes(packed(record) + b"\n")
    journal = Journal(path, cfg, wall=clock.wall)
    yield clock, cfg, path, journal
    journal.close()

class FakeProcess:
    def __init__(self):
        self.identity = {"pid": 4242, "created_100ns": 1234, "path": P.PYTHON, "job_member": True,
                         "argv_hash": "a" * 64, "config_hash": "b" * 64}
        self.flags, self.code, self.ended, self.valid, self.closed = set(), None, False, True, False
        self.stop_calls = self.kill_calls = 0
    def owned(self): return self.valid
    def poll(self): return self.ended, self.code
    def close(self): self.closed = True
    def graceful_stop(self): self.stop_calls += 1; return True, 0
    def terminate(self): self.kill_calls += 1; self.ended = True; self.code = 0xE0000001


def controller(setup, spawn=None, preflight=None):
    clock, cfg, path, journal = setup
    launched = []
    def create(role):
        child = FakeProcess()
        launched.append((role, child))
        return child
    s = Supervisor(journal, spawn or create, preflight or (lambda role: None),
                   wall=clock.wall, mono=clock.mono, free=lambda: 200 * GIB)
    return s, launched

@pytest.mark.parametrize("code", [0, 75, 78, 79, 2, 3, 137, 255, -1, -9, -15, 0xC0000005, -1073741819, 0xC000013A, None, True])
def test_terminal_cross_invocation(setup, code):
    clock, cfg, path, journal = setup
    s, launched = controller(setup)
    s.start("pmc")
    s.exit("pmc", code, set())
    expected = P.classify_exit(code)
    assert journal.state["pmc"]["reason"] == expected
    latch_id = journal.state["pmc"]["latch_id"]
    journal.close()
    clock.advance(100000)
    fresh = Journal(path, cfg, wall=clock.wall)
    new, launches = controller((clock, cfg, path, fresh))
    new.tick()
    assert not any(role == "pmc" for role, _ in launches)
    assert fresh.state["pmc"]["latch_id"] == latch_id
    fresh.close()

@pytest.mark.parametrize("attempt,expected", [(0,6),(1,12),(2,24),(3,48),(4,96),(5,192),(6,300),(100000,300)])
def test_backoff_constants(attempt, expected):
    assert P.backoff(attempt) == expected


def test_only_one_retries_monotonic_budget(setup):
    clock, cfg, path, journal = setup
    s, launched = controller(setup)
    s.start("pmc")
    for delay in (6, 12, 24):
        child = s.processes.pop("pmc")
        s.exit("pmc", 1, set())
        before = len(launched)
        clock.advance(delay - 0.01)
        s.tick()
        assert len([r for r,_ in launched if r == "pmc"]) == before - sum(r == "analyzer_viewer" for r,_ in launched[:before])
        clock.advance(0.01)
        s.tick()
        assert journal.state["pmc"]["phase"] == "RUNNING"
    assert journal.state["pmc"]["attempt"] == 3
    assert len(journal.state["pmc"]["reservations"]) == 3
    s.processes.pop("pmc")
    s.exit("pmc", 1, set())
    assert journal.state["pmc"]["reason"] == "RESTART_BUDGET_EXHAUSTED"
    clock.advance(1801)
    s.tick()
    assert journal.state["pmc"]["phase"] == "LATCHED"


def test_wall_forward_not_early_mono_retry(setup):
    clock, cfg, path, journal = setup
    s, _ = controller(setup)
    s.start("pmc"); s.processes.pop("pmc"); s.exit("pmc", 1, set())
    clock.t += 3600
    s.tick()
    assert journal.state["pmc"]["phase"] == "BACKOFF"
    clock.m += 6
    s.tick()
    assert journal.state["pmc"]["phase"] == "RUNNING"


def test_rolling_window_exact_boundary(setup):
    clock, cfg, path, journal = setup
    s, _ = controller(setup)
    journal.state["pmc"]["reservations"] = [clock.t-1800, clock.t-1799, clock.t-10]
    journal.state["pmc"]["phase"] = "BACKOFF"
    s.start("pmc", restart=True)
    assert len(journal.state["pmc"]["reservations"]) == 3
    assert journal.state["pmc"]["phase"] == "RUNNING"


def test_reset_only_healthy_owned_30_minutes(setup):
    clock, cfg, path, journal = setup
    s, _ = controller(setup)
    s.start("pmc", restart=True)
    s.collector_sample = {"status": "GREEN"}
    s.sample_mono = clock.m
    s.tick()
    clock.advance(1799); s.sample_mono = clock.m; s.tick()
    assert journal.state["pmc"]["attempt"] == 1
    clock.advance(1); s.sample_mono = clock.m; s.tick()
    assert journal.state["pmc"]["attempt"] == 0
    assert journal.state["pmc"]["reservations"]  # reset index is not budget reset

@pytest.mark.parametrize("fault", ["STARTUP_IMPORT_FAILURE", "FLY_SIDE_PATH_FAILURE"])
def test_refusal_even_if_exit_one(setup, fault):
    _,_,_,journal = setup
    s,_ = controller(setup); s.start("pmc"); s.exit("pmc", 1, {fault})
    assert journal.state["pmc"]["reason"] == fault

@pytest.mark.parametrize("phase", ["START_PENDING", "RUNNING", "EXIT_OBSERVED", "BACKOFF"])
def test_power_loss_unresolved_phase_latches(setup, phase):
    clock,cfg,path,journal = setup
    state = copy.deepcopy(journal.state); state["pmc"]["phase"] = phase
    journal.write("pmc", phase, state)
    journal.close()
    fresh = Journal(path, cfg, wall=clock.wall)
    assert fresh.state["pmc"]["reason"] == "UNCERTAIN_PREVIOUS_RUN"
    previous = path.read_bytes(); fresh.close()
    second = Journal(path, cfg, wall=clock.wall)
    assert path.read_bytes() == previous  # replay idempotence, no tick spam
    second.close()

@pytest.mark.parametrize("fault_at", ["START_PENDING", "RUNNING", "EXIT_OBSERVED", "BACKOFF", "TERMINAL_EXIT_0"])
def test_fsync_fault_cannot_launch_replacement(setup, fault_at):
    clock,cfg,path,journal = setup
    s, launched = controller(setup)
    real = journal.write
    def fail(role, reason, *a, **kw):
        if reason == fault_at:
            raise P.Hold("DURABILITY_FAILURE")
        return real(role, reason, *a, **kw)
    journal.write = fail
    try:
        s.start("pmc")
        if "pmc" in s.processes:
            s.exit("pmc", 0 if fault_at == "TERMINAL_EXIT_0" else 1, set())
    except P.Hold:
        pass
    assert len([r for r,_ in launched if r == "pmc"]) <= 1
    journal.close()
    fresh = Journal(path, cfg, wall=clock.wall)
    assert fresh.state["pmc"]["phase"] in {"NEVER_STARTED", "LATCHED"}
    fresh.close()


def test_failed_spawn_consumes_durable_reservation(setup):
    clock,cfg,path,journal = setup
    def bad(role): raise RuntimeError("credential-canary-do-not-report")
    s,_ = controller(setup, spawn=bad)
    s.start("pmc", restart=True)
    assert journal.state["pmc"]["reservations"] == [clock.t]
    assert journal.state["pmc"]["reason"] == "SPAWN_FAILURE"
    assert b"credential-canary" not in path.read_bytes()
    journal.close()
    fresh = Journal(path, cfg, wall=clock.wall)
    assert fresh.state["pmc"]["reservations"] == [clock.t]
    fresh.close()

@pytest.mark.parametrize("corruption", [b"", b"{", b"{}\n", b'wrong\n'])
def test_corrupt_torn_or_missing_no_rebootstrap(setup, corruption):
    clock,cfg,path,journal = setup; journal.close()
    path.write_bytes(corruption)
    with pytest.raises((P.Hold, OSError)):
        Journal(path, cfg, wall=clock.wall)
    assert path.read_bytes() == corruption
    path.unlink()
    with pytest.raises(OSError): Journal(path, cfg, wall=clock.wall)
    assert not path.exists()


def test_checksum_chain_config_identity_and_sync_failure(setup):
    clock,cfg,path,journal = setup
    blob = path.read_bytes(); row = json.loads(blob); row["state"]["pmc"]["attempt"] = 9
    journal.close(); path.write_bytes(packed(row)+b"\n")
    with pytest.raises(P.Hold): Journal(path,cfg,wall=clock.wall)
    path.write_bytes(blob)
    with pytest.raises(P.Hold): Journal(path,{**cfg,"implementation_sha256":"b"*64},wall=clock.wall)
    fresh=Journal(path,cfg,wall=clock.wall,sync=lambda _: (_ for _ in ()).throw(OSError("secret-canary")))
    with pytest.raises(P.Hold,match="DURABILITY_FAILURE"): fresh.write("pmc","START_PENDING")
    assert fresh.broken
    with pytest.raises(P.Hold): fresh.write("pmc","RUNNING")
    fresh.close()


def test_journal_cap_reserves_terminal_room(setup):
    clock,cfg,path,journal = setup
    size=path.stat().st_size; journal.cap=size+RESERVE+5
    with pytest.raises(P.Hold, match="JOURNAL_CAP"): journal.write("pmc","START_PENDING")
    journal.latch("pmc","JOURNAL_CAP")
    journal.latch("analyzer_viewer","JOURNAL_CAP")
    assert path.stat().st_size < journal.cap
    assert all(v["phase"]=="LATCHED" for v in journal.state.values())


def test_rollback_blocks_starts(setup):
    clock,_,_,journal=setup
    s,launched=controller(setup); clock.t-=1; s.tick()
    assert not launched and s.stop_event.is_set() and s.failure=="CLOCK_UNCERTAIN"


def test_pid_reuse_no_adopt_or_kill(setup):
    _,_,_,journal=setup
    s,launched=controller(setup); s.start("pmc"); p=s.processes["pmc"]; p.valid=False
    s.tick()
    assert journal.state["pmc"]["reason"]=="OWNERSHIP_FAILURE"
    assert p.kill_calls==0 and p.stop_calls==0


def test_startup_grace_preserves_live_viewer(setup):
    clock,_,_,journal=setup
    s,_=controller(setup); s.start("analyzer_viewer"); p=s.processes["analyzer_viewer"]
    clock.advance(119.99); s.tick(); assert journal.state["analyzer_viewer"]["phase"]=="RUNNING"
    clock.advance(.01); s.tick(); assert journal.state["analyzer_viewer"]["reason"]=="STARTUP_NOT_READY"
    assert p.stop_calls==p.kill_calls==0 and not p.closed


def test_fly_latch_no_ui_change_or_retry(setup):
    _,_,_,journal=setup
    s,_=controller(setup); s.start("analyzer_viewer"); p=s.processes["analyzer_viewer"]
    p.flags.add("FLY_SIDE_PATH_FAILURE"); s.tick()
    assert journal.state["analyzer_viewer"]["reason"]=="FLY_SIDE_PATH_FAILURE"
    assert not p.closed and p.stop_calls==p.kill_calls==0
    p.ended=True;p.code=1;s.tick()
    assert journal.state["analyzer_viewer"]["reason"]=="FLY_SIDE_PATH_FAILURE"
    assert journal.state["analyzer_viewer"]["exit"]["code"]==1

@pytest.mark.parametrize("enabled",[False,True])
def test_disk_stop_disabled_default_and_real_exit(setup, enabled):
    clock,cfg,_,journal=setup; cfg["disk_floor_terminate_enabled"]=enabled
    s,_=controller(setup);s.start("pmc");p=s.processes["pmc"]
    s.disk_sample={"free_bytes":19*GIB};s.sample_mono=clock.m;s.tick()
    assert journal.state["pmc"]["reason"]=="DISK_FLOOR"
    assert p.stop_calls==int(enabled)
    clock.advance(10);s.sample_mono=clock.m;s.tick()
    assert p.kill_calls==int(enabled)
    if enabled:
        s.tick();assert journal.state["pmc"]["exit"]["code"]==0xE0000001
        assert journal.state["pmc"]["exit"]["code"]!=75

@pytest.mark.parametrize("free",[None,20*GIB-1,20*GIB,100*GIB])
def test_start_disk_boundaries(setup, free):
    _,_,_,journal=setup;s,_=controller(setup);s.free=lambda:free;s.start("pmc")
    assert (journal.state["pmc"]["phase"]=="RUNNING")== (free is not None and free>=20*GIB)


def test_supervisor_stop_persists_both_roles(setup):
    clock,cfg,path,journal=setup;s,_=controller(setup)
    for role in P.ROLES: s.start(role);s.processes[role].ended=True;s.processes[role].code=0
    s.shutdown();journal.close()
    fresh=Journal(path,cfg,wall=clock.wall)
    assert all(v["phase"]=="LATCHED" and v["reason"]=="SUPERVISOR_SHUTDOWN" for v in fresh.state.values())
    fresh.close()


def test_root_contract_no_cleanup(tmp_path):
    for name in ("runtime.lock","launch.journal","runtime.journal"): (tmp_path/name).touch()
    (tmp_path/"2026-11-10").mkdir();P.validate_runtime_root(tmp_path)
    for name in ("stdout.log","health.json","pid","state.jsonl","2026-02-30","2026-11-09"):
        p=tmp_path/name;p.touch()
        before=sorted(x.name for x in tmp_path.iterdir())
        with pytest.raises(P.Hold,match="FOREIGN_RUNTIME_ROOT"): P.validate_runtime_root(tmp_path)
        assert sorted(x.name for x in tmp_path.iterdir())==before
        p.unlink()
    (tmp_path/"logs").mkdir()
    with pytest.raises(P.Hold): P.validate_runtime_root(tmp_path)


def test_reparse_and_case_containment(tmp_path):
    target=tmp_path/"real";target.mkdir();link=tmp_path/"alias"
    try: link.symlink_to(target,target_is_directory=True)
    except OSError: pytest.skip("worker cannot create symlinks")
    with pytest.raises(P.Hold): P.no_reparse(link)
    assert P.contained(r"c:\DOXXEDCRYPTO\data\collector-r2\a",P.ROOT)
    assert not P.contained(P.ROOT+"-logs",P.ROOT)
    fake=mock.Mock(st_mode=0,st_file_attributes=0x400)
    with mock.patch.object(Path,"lstat",return_value=fake):
        with pytest.raises(P.Hold): P.no_reparse(tmp_path)

@pytest.mark.parametrize("role",["showcase_ui.py","bot.main","bot.py","btc_conservative_agent.py","analyzer_research_engine_v62.py",
                                 "EXT2","forward","cmd.exe","python -c","pmc;bot",r"..\pmc","analyzer_viewer.cmd",None])
def test_arbitrary_role_injection_cannot_spawn(role):
    with pytest.raises(P.Hold): P.commands(role)
    with pytest.raises(P.Hold): P.child_env(role,{})


def test_exact_viewer_not_engine_and_parent_scrub():
    hostile={k:"secret-canary" for k in ("GH_TOKEN","bitfinex_key","BITFINEX_FEE","LIVE_ARMED","RELAY_ARMED","Path","PATH",
        "pYtHoNpAtH","PYTHONHOME","HTTPS_PROXY","DATABASE_URL","USERPROFILE","SYSTEMROOT","FLY_APP","COOKIE","API_KEY")}
    before=hostile.copy()
    for role in P.ROLES:
        env=P.child_env(role,hostile)
        assert not any(v=="secret-canary" for v in env.values())
        assert len({k.lower() for k in env})==len(env)
        assert env["FORCE_PAPER_MODE"]=="1" and env["ARROW_DEFAULT_MEMORY_POOL"]=="system"
        assert env["PYTHONDONTWRITEBYTECODE"]=="1"
        assert not set(env)&{"GH_TOKEN","LIVE_ARMED","FLY_APP","DATABASE_URL"}
    argv,cwd=P.commands("analyzer_viewer")
    assert argv==[P.PYTHON,"-B","-u",P.VIEWER+r"\research_dashboard.py","--standalone"]
    assert hostile==before


def test_unknown_config_keys_and_command_paths_rejected(tmp_path):
    base={"schema":1,"installation_id":"synthetic-install-123","genesis_sha256":"a"*64,
          "implementation_sha256":"b"*64,"source_sha256":{P.ENTRY:"d"*64},"reconciled_owners_receipt_sha256":"c"*64,
          "python_sha256":P.PYTHON_HASH,"disk_floor_terminate_enabled":False,"disk_floor_card_sha256":None,
          "human_clear_receipt_sha256":None,"human_clear_public_key_sha256":None}
    path=tmp_path/"cfg.json"
    path.write_text(json.dumps(base));assert P.load_config(path)==base
    for key in ("argv","engine","command","script","bind_host","python","env","force","ignore_latch"):
        path.write_text(json.dumps({**base,key:"bot.py"}))
        with pytest.raises(P.Hold):P.load_config(path)
    for inject in (P.VIEWER+r"\..\bot.py",r"C:\temp\bot.py",P.SCRIPTS+r"\launch.cmd"):
        path.write_text(json.dumps({**base,"source_sha256":{inject:"a"*64}}))
        with pytest.raises(P.Hold):P.load_config(path)
    path.write_text(json.dumps({**base,"disk_floor_terminate_enabled":True}))
    with pytest.raises(P.Hold):P.load_config(path)


def test_no_args_and_import_no_side_effects():
    root=Path(__file__).resolve().parents[1]
    with mock.patch("socket.socket",side_effect=AssertionError("network")), mock.patch("subprocess.Popen",side_effect=AssertionError("spawn")):
        spec=importlib.util.spec_from_file_location("entry",root/"pmc_analyzer_supervisor.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with pytest.raises(SystemExit) as err: module.main([])
        assert err.value.code==2


def test_cross_process_instance_lock(tmp_path):
    path=tmp_path/"permanent.lock";path.write_bytes(b"0")
    first=InstanceLock(path)
    script="from pathlib import Path;from pmc_supervisor.journal import InstanceLock;InstanceLock(Path(__import__('sys').argv[1]))"
    env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1])}
    result=subprocess.run([sys.executable,"-B","-c",script,str(path)],env=env,capture_output=True,timeout=10)
    assert result.returncode!=0
    first.close()
    second=InstanceLock(path);second.close();assert path.read_bytes()==b"0"


def test_signed_human_clear_records_who_why_and_is_idempotent(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
    from pmc_supervisor.recovery import verify_signature
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    numbers=key.public_key().public_numbers()
    public={"n":format(numbers.n,"x"),"e":numbers.e}
    key_path=tmp_path/"human-clear-public-key.json";key_path.write_bytes(P.packed(public))
    clock=Clock()
    cfg={"schema":1,"implementation_sha256":"a"*64,"genesis_sha256":"", "human_clear_receipt_sha256":None,
         "human_clear_public_key_sha256":P.file_hash(key_path)}
    gen=genesis_record(cfg,clock.t,"explicit-human-test-install");cfg["genesis_sha256"]=gen["hash"]
    path=tmp_path/"supervisor-state.jsonl";path.write_bytes(P.packed(gen)+b"\n")
    j=Journal(path,cfg,wall=clock.wall);j.latch("pmc","TERMINAL_EXIT_0")
    prior=j.last["hash"];lid=j.state["pmc"]["latch_id"];j.close()
    body={"role":"pmc","latch_id":lid,"previous_hash":prior,"new_config_hash":config_identity(cfg),
          "new_code_hash":cfg["implementation_sha256"],"roots":[P.ROOT,P.DATA,P.REPORTS],
          "who":"Human operator","why":"Reviewed terminal stop","remediation":"Root and source pins verified"}
    sig=key.sign(P.packed(body),padding.PKCS1v15(),hashes.SHA256()).hex()
    assert verify_signature(body,sig,public)
    assert not verify_signature({**body,"who":"Forged operator"},sig,public)
    receipt={"body":body,"signature":sig};rp=tmp_path/"human-clear.json";rp.write_bytes(P.packed(receipt))
    cfg["human_clear_receipt_sha256"]=P.file_hash(rp)
    j=Journal(path,cfg,wall=clock.wall)
    assert j.state["pmc"]["phase"]=="NEVER_STARTED"
    assert j.last["reason"]=="HUMAN_CLEAR" and j.last["human"]["who"]=="Human operator"
    # Recovery evidence remains replay-valid after subsequent transitions.
    j.latch("pmc","SUPERVISOR_SHUTDOWN");after=path.read_bytes();j.close()
    fresh=Journal(path,cfg,wall=clock.wall)
    assert path.read_bytes()==after and fresh.state["pmc"]["phase"]=="LATCHED"
    fresh.close()
    # A different signing key cannot be installed via ordinary config drift.
    with pytest.raises(P.Hold):Journal(path,{**cfg,"human_clear_public_key_sha256":"f"*64},wall=clock.wall)
