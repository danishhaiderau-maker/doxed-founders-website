"""REAL Windows x64 APIs, synthetic children only. CI is not a laptop PASS."""
import ctypes as C
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pmc_supervisor.win32 import API, OwnedProcess, PROTOTYPES, DWORD
from pmc_supervisor.policy import Hold

pytestmark=pytest.mark.skipif(os.name!="nt",reason="requires real Windows x64 API worker")


def wait_exit(child, timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        ended,code=child.poll()
        if ended:return code
        time.sleep(.02)
    raise AssertionError("bounded synthetic child did not exit")


def start(tmp_path, code, *, hidden=True):
    # Explicit injected test seam. These commands are not reachable via config/CLI.
    script=tmp_path/"fixture.py";script.write_text(code)
    stdout,stderr=tmp_path/"out.log",tmp_path/"err.log";stdout.touch();stderr.touch()
    env={"SYSTEMROOT":os.environ["SYSTEMROOT"],"WINDIR":os.environ["SYSTEMROOT"],
         "PYTHONDONTWRITEBYTECODE":"1","PYTHONUNBUFFERED":"1","PYTHONIOENCODING":"utf-8",
         "ARROW_DEFAULT_MEMORY_POOL":"system"}
    child=OwnedProcess(API(),[sys.executable,"-B","-u",str(script)],str(tmp_path),env,"a"*64,stdout,stderr,hidden=hidden)
    return child,stdout,stderr

@pytest.mark.parametrize("code",[0,1,75,78,79,2,137,255,0xC0000005])
def test_real_job_membership_exit_codes_and_redirected_utf8(tmp_path,code):
    child,out,err=start(tmp_path,f'import ctypes as C;print("synthetic utf8 ✓",flush=True);k=C.WinDLL("kernel32",use_last_error=True);f=k.ExitProcess;f.argtypes=[C.c_uint32];f.restype=None;f({code})')
    try:
        assert C.sizeof(C.c_void_p)==8
        assert child.owned() and child.identity["job_member"]
        assert child.identity["created_100ns"]>0 and child.identity["pid"]>0
        assert wait_exit(child)==code
        assert "✓" in out.read_text(encoding="utf-8")
    finally:child.close()

@pytest.mark.parametrize("hidden",[False,True])
def test_python311_arrow_system_pool_and_window_styles(tmp_path,hidden):
    code='import sys,pyarrow as pa;assert sys.version_info[:2]==(3,11);assert pa.default_memory_pool().backend_name=="system";print("ARROW_SYSTEM_OK",flush=True)'
    child,out,err=start(tmp_path,code,hidden=hidden)
    try:
        assert wait_exit(child)==0
        assert "ARROW_SYSTEM_OK" in out.read_text()
    finally:child.close()


def test_remove_every_real_ctypes_prototype_asserts_not_timeout():
    api=API()
    for name,(args,result) in PROTOTYPES.items():
        fn=getattr(api.dll,name);fn.argtypes=None
        with pytest.raises(AssertionError,match="missing Win32 declaration"):api.call(name)
        fn.argtypes=args;fn.restype=C.c_int if result is not C.c_int else None
        with pytest.raises(AssertionError,match="missing Win32 declaration"):api.call(name)
        fn.restype=result
    api.assert_prototypes()


def test_denied_access_priority_and_handle_accounting(tmp_path):
    api=API()
    query=api.checked("OpenProcess",0x1000,False,os.getpid())
    try:
        # A real limited-query process handle cannot change priority.
        with pytest.raises(Hold):api.checked("SetPriorityClass",query,0x4000)
        assert api.last_error==5
    finally:api.close(query)
    own=api.checked("OpenProcess",0x400,False,os.getpid())
    try:
        before=DWORD();api.checked("GetProcessHandleCount",own,C.byref(before))
        for i in range(5):
            folder=tmp_path/str(i);folder.mkdir()
            child,_,_=start(folder,"pass")
            assert wait_exit(child)==0;child.close()
        after=DWORD();api.checked("GetProcessHandleCount",own,C.byref(after))
        assert after.value<=before.value+2
    finally:api.close(own)


def test_owned_stop_real_termination_code_and_lock_release(tmp_path):
    child,_,_=start(tmp_path,'import time;time.sleep(60)')
    try:
        ok,error=child.graceful_stop()
        assert type(ok) is bool and type(error) is int
        # Real Ctrl-Break is session-dependent; preserve actual observed code.
        ended,code=child.poll()
        if not ended:child.terminate()
        assert wait_exit(child) in (0xE0000001,0xC000013A,1)
    finally:child.close()


def test_supervisor_kill_job_closes_without_orphans(tmp_path):
    fixture=tmp_path/"fixture.py";fixture.write_text("import time;time.sleep(60)")
    out,err=tmp_path/"out.log",tmp_path/"err.log";out.touch();err.touch()
    receipt=tmp_path/"receipt.txt"
    driver=tmp_path/"driver.py"
    driver.write_text('''import os,sys,time
from pmc_supervisor.win32 import API,OwnedProcess
child=OwnedProcess(API(),[sys.executable,"-B",sys.argv[1]],os.path.dirname(sys.argv[1]),{"SYSTEMROOT":os.environ["SYSTEMROOT"]},"a"*64,sys.argv[2],sys.argv[3])
open(sys.argv[4],"w").write(str(child.identity["pid"]))
time.sleep(60)
''')
    parent=subprocess.Popen([sys.executable,"-B",str(driver),str(fixture),str(out),str(err),str(receipt)],
        env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1])},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    observer=None;api=API()
    try:
        deadline=time.monotonic()+15
        while not receipt.exists() and parent.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert receipt.exists(),"synthetic owner failed before receipt"
        observer=api.checked("OpenProcess",0x100000|0x1000,False,int(receipt.read_text()))
        parent.kill();parent.wait(10)
        assert api.call("WaitForSingleObject",observer,10000)==0
        code=DWORD();api.checked("GetExitCodeProcess",observer,C.byref(code))
        assert code.value!=259
    finally:
        if parent.poll() is None:parent.kill();parent.wait(10)
        if observer:api.close(observer)
        if parent.stderr:parent.stderr.close()


def test_real_junction_refused_without_cleanup(tmp_path):
    from pmc_supervisor.policy import no_reparse
    target=tmp_path/"target";target.mkdir();alias=tmp_path/"junction"
    result=subprocess.run(["cmd.exe","/c","mklink","/J",str(alias),str(target)],capture_output=True,timeout=10)
    assert result.returncode==0
    with pytest.raises(Hold):no_reparse(alias)
    assert alias.exists() and target.exists()


def test_real_console_callback_lifetime_flush_and_directory_pins(tmp_path):
    import msvcrt
    api=API()
    callback_type=C.WINFUNCTYPE(C.c_int32,C.c_uint32)
    callback=callback_type(lambda event:0)
    api.checked("SetConsoleCtrlHandler",C.cast(callback,C.c_void_p),True)
    try:
        path=tmp_path/"durable.log"
        with path.open("wb") as f:
            f.write(b"receipt\n");f.flush()
            api.checked("FlushFileBuffers",msvcrt.get_osfhandle(f.fileno()))
        handle=api.pin_file(path)
        try:
            with pytest.raises(PermissionError):path.write_bytes(b"overwrite")
        finally:api.close(handle)
        handle=api.pin_directory(tmp_path);api.close(handle)
    finally:
        api.checked("SetConsoleCtrlHandler",C.cast(callback,C.c_void_p),False)
