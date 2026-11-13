"""Read-only loader role for a separately approved, prestarted supervisor child.

This module NEVER starts a process/thread/timer at import or from HTTP. The
external supervisor supplies its prestarted child/pipe, calls tick on its OWN
5s timer, and owns stop/reap. Windows cancellation/IPC/atomic-publish proof and
exact chain bindings remain required; no runtime enablement is claimed here.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path

from research import twoyear_batch_view as V

_SLOT=threading.Lock()
_LAST_ATTEMPT=None


def read_capped(root, *, opener=None):
    root=Path(root)
    path=root/V.SNAPSHOT_NAME
    reparse=getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",0x400)
    candidates=[path,*path.parents]
    if any(p.is_symlink() or getattr(p.lstat(),"st_file_attributes",0)&reparse for p in candidates) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("snapshot must be a regular in-root non-symlink file")
    with (opener(path) if opener else path.open("rb")) as handle:
        before=os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("snapshot is not a regular file")
        # fstat size is NOT the bound: growth can race it. Actual read is cap+1.
        buffer=bytearray()
        while len(buffer)<V.BYTE_CAP+1:
            chunk=handle.read(min(65536,V.BYTE_CAP+1-len(buffer)))
            if not chunk:
                break
            buffer.extend(chunk)
        raw=bytes(buffer)
        after=os.fstat(handle.fileno())
        if len(raw)>V.BYTE_CAP:
            raise ValueError("byte cap before JSON parse")
        if before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns:
            raise ValueError("in-place/growing publication; require temp+atomic rename")
    V.validate(raw)
    return raw


def loader_child(connection):
    """Supervisor process target. ONE job at a time, bounded local bytes only."""
    try:
        while True:
            command=connection.recv_bytes(4096)
            if command==b"STOP":
                return
            root=command.decode("utf-8")
            try:
                connection.send_bytes(b"O"+read_capped(root))
            except Exception as exc:
                message=(type(exc).__name__+": "+str(exc))[:1024].encode("utf-8")
                connection.send_bytes(b"E"+message)
    except (EOFError,OSError):
        return
    finally:
        connection.close()


class SupervisorCacheAdapter:
    """No start/restart API. Consumes a supervisor-owned PRESTARTED child.

    At most one GLOBAL attempt per 5s even after failures and across generations.
    On timeout/failure it latches; another GET/tick cannot retry or replace it.
    An external owner may stop a blocked child. Linux fixtures exercise bounded
    terminate/reap; target Windows proof is deliberately not inferred from them.
    """
    def __init__(self,process,connection,root,store,*,platform_verified=False,clock=time.monotonic):
        self.process,self.connection,self.root,self.store=process,connection,str(root),store
        self.platform_verified,self.clock=platform_verified,clock
        self.latched=False
        self.stopped_and_reaped=None
        self.stop_elapsed=None

    def _stop(self):
        start=self.clock()
        self.latched=True
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=.025)
        self.stopped_and_reaped=not self.process.is_alive()
        self.stop_elapsed=self.clock()-start
        self.latched=True
        self.store.reason="loader UNAVAILABLE / latched; cancellation/reap "+str(self.stopped_and_reaped)
        try:
            self.connection.close()
        except OSError:
            pass

    def tick(self):
        global _LAST_ATTEMPT
        if not self.platform_verified:
            self.store.reason="real-data refresh HOLD: target platform/chain/IPC cancellation proof missing"
            return False
        if self.latched or not _SLOT.acquire(blocking=False):
            return False
        try:
            now=self.clock()
            if _LAST_ATTEMPT is not None and now-_LAST_ATTEMPT<5:
                return False
            _LAST_ATTEMPT=now
            if not self.process.is_alive():
                self.latched=True
                self.store.reason="prestarted loader unavailable; no automatic restart"
                return False
            # The supervisor's one watchdog owns cancellation. It is NOT a
            # handler timeout and never creates/replaces a read worker.
            timed_out=threading.Event()
            stopping=threading.Lock()
            stopped=threading.Event()
            def expire():
                if stopping.acquire(blocking=False):
                    timed_out.set()
                    try:
                        self._stop()
                    finally:
                        stopped.set()
            timer=threading.Timer(.100,expire)
            timer.daemon=True
            timer.start()
            try:
                self.connection.send_bytes(self.root.encode("utf-8"))
                if not self.connection.poll(.100):
                    expire()
                    return False
                response=self.connection.recv_bytes(V.BYTE_CAP+1)
                if timed_out.is_set():
                    return False
                if not response.startswith(b"O"):
                    self.store.reason=response[1:].decode("utf-8",errors="replace")
                    self.latched=True
                    return False
                # Loader open/read/parse/IPC is finished; publication/rendering
                # is a supervisor operation, never part of an HTTP handler.
                timer.cancel()
                self.store.publish(response[1:])
                return not timed_out.is_set()
            except (EOFError,OSError,ValueError) as exc:
                self.store.reason="loader failure: "+str(exc)[:1024]
                self.latched=True
                return False
            finally:
                timer.cancel()
                if timed_out.is_set():
                    stopped.wait(.025)
        finally:
            _SLOT.release()

    def run(self,stop_event):
        """Supervisor's independently scheduled loop; no GET ever calls this.

        Caller owns the existing coordinator thread/process and stop event.
        Missed ticks drop (no catch-up queue); a latch is never reset here.
        """
        next_tick=self.clock()
        while not stop_event.wait(max(0.0,next_tick-self.clock())):
            self.tick()
            next_tick=self.clock()+5.0
