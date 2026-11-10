"""Windows x64 owned processes. All APIs declared before use.

Windows 10+ job-list process attribute puts the child IN the kill-on-close Job
at creation, not in a later assign race. Handle-list attribute restricts inherited
handles to stdin/stdout/stderr. Suspended start permits membership/path/priority
verification before any Python instruction. There is no subprocess/shell fallback.
"""
from __future__ import annotations
import ctypes as C
import os
import subprocess
import threading
from pathlib import Path
from .policy import Hold, digest, no_reparse

DWORD = C.c_uint32
BOOL = C.c_int32
WORD = C.c_uint16
HANDLE = C.c_void_p
SIZE_T = C.c_size_t
LPVOID = C.c_void_p
ULONG_PTR = C.c_size_t

class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", LPVOID), ("bInheritHandle", BOOL)]

class STARTUPINFO(C.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", C.c_wchar_p), ("lpDesktop", C.c_wchar_p),
                ("lpTitle", C.c_wchar_p), ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD),
                ("dwYSize", DWORD), ("dwXCountChars", DWORD), ("dwYCountChars", DWORD),
                ("dwFillAttribute", DWORD), ("dwFlags", DWORD), ("wShowWindow", WORD),
                ("cbReserved2", WORD), ("lpReserved2", LPVOID),
                ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE)]

class STARTUPINFOEX(C.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", LPVOID)]

class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", DWORD), ("dwThreadId", DWORD)]

class FILETIME(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]

class BASIC_LIMIT(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
                ("ActiveProcessLimit", DWORD), ("Affinity", ULONG_PTR), ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]

class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                              "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

class EXTENDED_LIMIT(C.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", SIZE_T),
                ("JobMemoryLimit", SIZE_T), ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]

# This table is also the mutation-test inventory. No WinDLL symbol is called
# anywhere except through this declared set.
PROTOTYPES = {
    "CreateJobObjectW": ([C.POINTER(SECURITY_ATTRIBUTES), C.c_wchar_p], HANDLE),
    "SetInformationJobObject": ([HANDLE, C.c_int, LPVOID, DWORD], BOOL),
    "IsProcessInJob": ([HANDLE, HANDLE, C.POINTER(BOOL)], BOOL),
    "CreateProcessW": ([C.c_wchar_p, C.c_wchar_p, C.POINTER(SECURITY_ATTRIBUTES), C.POINTER(SECURITY_ATTRIBUTES),
                        BOOL, DWORD, LPVOID, C.c_wchar_p, C.POINTER(STARTUPINFOEX), C.POINTER(PROCESS_INFORMATION)], BOOL),
    "InitializeProcThreadAttributeList": ([LPVOID, DWORD, DWORD, C.POINTER(SIZE_T)], BOOL),
    "UpdateProcThreadAttribute": ([LPVOID, DWORD, SIZE_T, LPVOID, SIZE_T, LPVOID, C.POINTER(SIZE_T)], BOOL),
    "DeleteProcThreadAttributeList": ([LPVOID], None),
    "CreatePipe": ([C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(SECURITY_ATTRIBUTES), DWORD], BOOL),
    "SetHandleInformation": ([HANDLE, DWORD, DWORD], BOOL),
    "CreateFileW": ([C.c_wchar_p, DWORD, DWORD, C.POINTER(SECURITY_ATTRIBUTES), DWORD, DWORD, HANDLE], HANDLE),
    "GetProcessTimes": ([HANDLE, C.POINTER(FILETIME), C.POINTER(FILETIME), C.POINTER(FILETIME), C.POINTER(FILETIME)], BOOL),
    "QueryFullProcessImageNameW": ([HANDLE, DWORD, C.c_wchar_p, C.POINTER(DWORD)], BOOL),
    "GetProcessId": ([HANDLE], DWORD),
    "OpenProcess": ([DWORD, BOOL, DWORD], HANDLE),
    "GetProcessHandleCount": ([HANDLE, C.POINTER(DWORD)], BOOL),
    "GetPriorityClass": ([HANDLE], DWORD),
    "SetPriorityClass": ([HANDLE, DWORD], BOOL),
    "ResumeThread": ([HANDLE], DWORD),
    "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
    "GetExitCodeProcess": ([HANDLE, C.POINTER(DWORD)], BOOL),
    "GenerateConsoleCtrlEvent": ([DWORD, DWORD], BOOL),
    "TerminateProcess": ([HANDLE, DWORD], BOOL),
    "CloseHandle": ([HANDLE], BOOL),
    "FlushFileBuffers": ([HANDLE], BOOL),
    "SetConsoleCtrlHandler": ([LPVOID, BOOL], BOOL),
}

class API:
    def __init__(self, dll=None):
        if dll is None:
            if os.name != "nt":
                raise Hold()
            dll = C.WinDLL("kernel32", use_last_error=True)
        self.dll = dll
        for name, (args, restype) in PROTOTYPES.items():
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, restype
        self.assert_prototypes()

    def assert_prototypes(self):
        for name, (args, result) in PROTOTYPES.items():
            fn = getattr(self.dll, name)
            assert fn.argtypes == args and fn.restype == result, "missing Win32 declaration: " + name

    def call(self, name, *args):
        assert name in PROTOTYPES, "missing Win32 declaration: " + name
        self.assert_prototypes()  # mutations fail BEFORE an undeclared API invocation
        return getattr(self.dll, name)(*args)

    def checked(self, name, *args):
        result = self.call(name, *args)
        if not result:
            error = C.get_last_error()  # capture immediately, never arbitrary exception text
            self.last_error = int(error)
            raise Hold("OWNERSHIP_FAILURE")
        return result

    def close(self, handle):
        if handle and handle != C.c_void_p(-1).value:
            self.checked("CloseHandle", handle)

    def pin_file(self, path):
        no_reparse(Path(path))
        # Share READ only. No source/config overwrite or rename while executing.
        h = self.call("CreateFileW", str(path), 0x80000000, 1, None, 3, 0x80, None)
        if not h or h == C.c_void_p(-1).value:
            self.last_error = C.get_last_error()
            raise Hold("IDENTITY_MISMATCH")
        return h

    def pin_directory(self, path):
        no_reparse(Path(path))
        h = self.call("CreateFileW", str(path), 0x80000000, 1, None, 3, 0x02000000, None)
        if not h or h == C.c_void_p(-1).value:
            self.last_error = C.get_last_error()
            raise Hold("IDENTITY_MISMATCH")
        return h


class LogDrain:
    """Bounded append-only raw log, safe classification flags only escape this class."""
    CAP = 8 * 1024 * 1024
    def __init__(self, stream, destination, flags):
        self.stream, self.destination, self.flags = stream, destination, flags
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        previous = b""
        try:
            no_reparse(self.destination)
            fd = os.open(self.destination, os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0))
            try:
                size = os.fstat(fd).st_size
                while True:
                    chunk = self.stream.read(4096)
                    if not chunk:
                        break
                    scan = (previous + chunk).lower()
                    if any(x in scan for x in (b"importerror", b"modulenotfounderror", b"syntaxerror", b"dll load failed", b"fatal python error")):
                        self.flags.add("STARTUP_IMPORT_FAILURE")
                    if b"fly" in scan and any(x in scan for x in (b"error", b"timeout", b"timed out", b"failed", b"exception")):
                        self.flags.add("FLY_SIDE_PATH_FAILURE")
                    if b"foreign_runtime_root" in scan:
                        self.flags.add("FOREIGN_RUNTIME_ROOT")
                    previous = scan[-1024:]
                    allowed = max(0, self.CAP - size)
                    view = memoryview(chunk[:allowed])
                    while view:
                        n = os.write(fd, view)
                        if n <= 0:
                            raise OSError()
                        view = view[n:]
                        size += n
                    if len(chunk) > allowed:
                        self.flags.add("LOG_CAP")  # keep draining; never block exit monitoring
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception:
            self.flags.add("DURABILITY_FAILURE")
        finally:
            self.stream.close()


class OwnedProcess:
    def __init__(self, api, argv, cwd, env, config_hash, stdout, stderr, *, hidden=True):
        self.api, self.job, self.process, self.thread = api, None, None, None
        self.flags, self.drains = set(), []
        self.identity = None
        keep, readers, attr_initialized = [], [], False
        attrs = None
        try:
            self.job = api.checked("CreateJobObjectW", None, None)
            limit = EXTENDED_LIMIT()
            limit.BasicLimitInformation.LimitFlags = 0x2000 | 0x8  # KILL_ON_JOB_CLOSE + ACTIVE_PROCESS
            limit.BasicLimitInformation.ActiveProcessLimit = 1  # neither child may spawn another engine
            api.checked("SetInformationJobObject", self.job, 9, C.byref(limit), C.sizeof(limit))
            sa = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), None, 1)
            for _ in range(2):
                read, write = HANDLE(), HANDLE()
                api.checked("CreatePipe", C.byref(read), C.byref(write), C.byref(sa), 0)
                readers.append(read.value)
                keep.append(write.value)
                api.checked("SetHandleInformation", read, 1, 0)
            stdin = api.call("CreateFileW", "NUL", 0x80000000, 3, C.byref(sa), 3, 0x80, None)
            if not stdin or stdin == C.c_void_p(-1).value:
                api.last_error = C.get_last_error()
                raise Hold("OWNERSHIP_FAILURE")
            keep.append(stdin)
            size = SIZE_T()
            api.call("InitializeProcThreadAttributeList", None, 2, 0, C.byref(size))
            error = C.get_last_error()
            if size.value == 0 or error != 122:  # ERROR_INSUFFICIENT_BUFFER is the expected sizing result
                raise Hold("OWNERSHIP_FAILURE")
            attrs = C.create_string_buffer(size.value)
            api.checked("InitializeProcThreadAttributeList", attrs, 2, 0, C.byref(size))
            attr_initialized = True
            handles = (HANDLE * len(keep))(*keep)
            jobs = (HANDLE * 1)(self.job)
            api.checked("UpdateProcThreadAttribute", attrs, 0, 0x20002, C.cast(handles, LPVOID), C.sizeof(handles), None, None)
            api.checked("UpdateProcThreadAttribute", attrs, 0, 0x2000D, C.cast(jobs, LPVOID), C.sizeof(jobs), None, None)
            si, pi = STARTUPINFOEX(), PROCESS_INFORMATION()
            si.StartupInfo.cb = C.sizeof(si)
            si.StartupInfo.dwFlags = 0x100 | 1  # std handles and show-window
            si.StartupInfo.wShowWindow = 0 if hidden else 5
            si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = stdin, keep[0], keep[1]
            si.lpAttributeList = C.cast(attrs, LPVOID)
            block = C.create_unicode_buffer("\0".join(k + "=" + v for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0\0")
            line = C.create_unicode_buffer(subprocess.list2cmdline(argv))
            # SUSPENDED | NEW_PROCESS_GROUP | UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO
            api.checked("CreateProcessW", argv[0], line, None, None, True, 0x4 | 0x200 | 0x400 | 0x80000,
                        C.cast(block, LPVOID), cwd, C.byref(si), C.byref(pi))
            self.process, self.thread, self.pid = pi.hProcess, pi.hThread, int(pi.dwProcessId)
            member = BOOL()
            api.checked("IsProcessInJob", self.process, self.job, C.byref(member))
            if not member.value:
                raise Hold("OWNERSHIP_FAILURE")
            api.checked("SetPriorityClass", self.process, 0x4000)  # BELOW_NORMAL; fail closed on denial
            if api.call("GetPriorityClass", self.process) != 0x4000:
                raise Hold("OWNERSHIP_FAILURE")
            self.identity = self.read_identity()
            if os.path.normcase(self.identity["path"]) != os.path.normcase(argv[0]):
                raise Hold("IDENTITY_MISMATCH")
            self.identity.update(argv_hash=digest(argv), config_hash=config_hash, job_member=True)
            import msvcrt
            for index, path in enumerate((stdout, stderr)):
                fd = msvcrt.open_osfhandle(readers[index], os.O_RDONLY | os.O_BINARY)
                readers[index] = None  # fd now owns the handle
                self.drains.append(LogDrain(os.fdopen(fd, "rb", buffering=0), Path(path), self.flags))
            if api.call("ResumeThread", self.thread) == 0xFFFFFFFF:
                api.last_error = C.get_last_error()
                raise Hold("OWNERSHIP_FAILURE")
            api.close(self.thread)
            self.thread = None
        except BaseException:
            self.close()  # closes only our Job/handles, never by name/port/PID
            raise
        finally:
            if attr_initialized:
                api.call("DeleteProcThreadAttributeList", attrs)
            for h in keep + readers:
                api.close(h)

    def read_identity(self):
        created, exited, kernel, user = (FILETIME() for _ in range(4))
        self.api.checked("GetProcessTimes", self.process, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user))
        path, length = C.create_unicode_buffer(32768), DWORD(32768)
        self.api.checked("QueryFullProcessImageNameW", self.process, 0, path, C.byref(length))
        pid = self.api.call("GetProcessId", self.process)
        if not pid:
            raise Hold("OWNERSHIP_FAILURE")
        return {"pid": int(pid), "created_100ns": (created.high << 32) | created.low, "path": path.value}

    def owned(self):
        identity = self.read_identity()
        member = BOOL()
        self.api.checked("IsProcessInJob", self.process, self.job, C.byref(member))
        return bool(member.value and all(identity[k] == self.identity[k] for k in identity))

    def poll(self):
        result = self.api.call("WaitForSingleObject", self.process, 0)
        if result == 258:
            return False, None
        if result != 0:
            raise Hold("OWNERSHIP_FAILURE")
        code = DWORD()
        self.api.checked("GetExitCodeProcess", self.process, C.byref(code))
        for drain in self.drains:
            drain.thread.join(1)
            if drain.thread.is_alive():
                self.flags.add("DURABILITY_FAILURE")
        return True, int(code.value)  # preserve unsigned NT termination statuses

    def graceful_stop(self):
        if not self.owned():
            raise Hold("OWNERSHIP_FAILURE")
        # May fail in noninteractive Task sessions: record no fabricated success.
        success = self.api.call("GenerateConsoleCtrlEvent", 1, self.pid)
        error = C.get_last_error() if not success else 0
        return bool(success), int(error)

    def terminate(self):
        if not self.owned():
            raise Hold("OWNERSHIP_FAILURE")
        self.api.checked("TerminateProcess", self.process, 0xE0000001)

    def close(self):
        # Job kill-on-close closes the orphan interval even on supervisor death.
        for attr in ("job", "thread", "process"):
            h = getattr(self, attr, None)
            if h:
                self.api.close(h)
                setattr(self, attr, None)
