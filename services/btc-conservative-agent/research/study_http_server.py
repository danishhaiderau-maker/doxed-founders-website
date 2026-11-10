"""Local standalone HTTP transport: admission BEFORE threads; bounded sockets.

Only cached bytes are served by the Flask study routes. This server never
starts a data loader, timer service, supervisor, collector or trading engine.
Target Windows latency/stop evidence remains a separate OPS/engineering gate.
"""
from __future__ import annotations

import socket
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from research import twoyear_batch_view as V


class _AbsoluteReader(io.RawIOBase):
    def __init__(self,sock,deadline):
        self.sock,self.deadline=sock,deadline
    def readable(self):
        return True
    def readinto(self,buffer):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError("absolute header deadline")
        self.sock.settimeout(min(.050,remaining))
        return self.sock.recv_into(buffer)


class _HeaderReader:
    def __init__(self,reader):
        self.reader,self.used,self.lines=reader,0,0
    def readline(self,size=-1):
        self.lines+=1
        remaining=8192-self.used
        if remaining<=0 or self.lines>34:
            raise ValueError("request header cap")
        data=self.reader.readline(min(size if size>=0 else remaining,remaining,2049))
        self.used+=len(data)
        if len(data)>2048:
            raise ValueError("request line/header field cap")
        return data
    def __getattr__(self,name):
        return getattr(self.reader,name)


class _DeadlineWriter:
    def __init__(self,sock,deadline):
        self.sock,self.deadline=sock,deadline
    def write(self,data):
        sent=0
        while sent<len(data):
            remaining=self.deadline-time.monotonic()
            if remaining<=0:
                raise TimeoutError("absolute response deadline")
            self.sock.settimeout(min(.050,remaining))
            n=self.sock.send(memoryview(data)[sent:sent+16384])
            if not n:
                raise ConnectionError("closed response socket")
            sent+=n
        return sent
    def flush(self): pass
    def close(self): pass
    @property
    def closed(self): return False


class StudyHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.0"
    def log_message(self,*args):
        pass
    def setup(self):
        self.connection=self.request
        accepted=self.server._connections[self.request][0]
        self.rfile=_HeaderReader(io.BufferedReader(_AbsoluteReader(self.connection,accepted+.050),buffer_size=8192))
        self.wfile=_DeadlineWriter(self.connection,accepted+.250)
    def handle(self):
        try:
            super().handle()
        except (ValueError,TimeoutError,ConnectionError,OSError):
            self.close_connection=True
    def send_error(self,code,message=None,explain=None):
        body,status,kind=V.empty_response(message or "HTTP error",status=code)
        self.close_connection=True
        self.send_response(status)
        self.send_header("Content-Type",kind)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Connection","close")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self._respond("GET")
    def do_HEAD(self):
        self._respond("HEAD")
    def do_POST(self):
        self._respond("POST")
    def do_OPTIONS(self):
        self._respond("OPTIONS")
    def _respond(self,method):
        if len(self.path)>2048:
            body,status,kind=V.empty_response("request path cap",status=400)
        else:
            with self.server.app.test_client() as client:
                response=client.open(self.path,method=method,environ_base={"REMOTE_ADDR":self.client_address[0]})
                body=response.get_data()
                status,kind=response.status_code,response.content_type
        if len(body)>(V.HTML_CAP if kind.startswith("text/html") else V.BYTE_CAP):
            body,status,kind=V.empty_response("response cap",status=503)
        self.close_connection=True
        self.send_response(status)
        self.send_header("Content-Type",kind)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Connection","close")
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        if method!="HEAD":
            self.wfile.write(body)


class StudyHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=8
    allow_reuse_address=False
    def __init__(self,address,app):
        if address!=("127.0.0.1",9001):
            raise ValueError("standalone must bind literal 127.0.0.1:9001")
        self.app=app
        app.test_client(use_cookies=False)  # prewarm package metadata before any handler
        self._admission=threading.BoundedSemaphore(7)
        self._connections={}
        self._guard=threading.Lock()
        super().__init__(address,StudyHandler)

    def process_request(self,request,address):
        # Called on the accept loop, BEFORE ThreadingMixIn spawns a thread.
        if not self._admission.acquire(blocking=False):
            try:
                request.settimeout(.010)
                body=V.BANNER.encode()
                request.send(b"HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\nContent-Length: "+str(len(body)).encode()+b"\r\n\r\n"+body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        request.settimeout(.050)
        with self._guard:
            self._connections[request]=(time.monotonic(),time.monotonic()+.225)
        try:
            super().process_request(request,address)
        except BaseException:
            with self._guard:
                self._connections.pop(request,None)
            self._admission.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self,request,address):
        try:
            super().process_request_thread(request,address)
        finally:
            with self._guard:
                self._connections.pop(request,None)
            self._admission.release()

    def service_actions(self):
        # Accept-loop watchdog closes stalled read/write sockets; it does NOT
        # claim to kill a Python handler. Cached handlers have no source I/O.
        now=time.monotonic()
        with self._guard:
            expired=[s for s,(_,deadline) in self._connections.items() if deadline<=now]
        for sock in expired:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.shutdown_request(sock)
