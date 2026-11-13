"""Real box loopback transport tests; not Windows/laptop acceptance."""
import socket
import threading
import time

import pytest
from http.server import ThreadingHTTPServer
from research import study_http_server as H
from research import research_dashboard as D
from research import twoyear_batch_view as V


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setitem(D.app.config,"RESEARCH_OFFLINE_VIEWER",True)
    monkeypatch.setattr(V,"STORE",V.SnapshotStore())
    original=ThreadingHTTPServer.__init__
    def ephemeral(self,address,handler):
        original(self,("127.0.0.1",0),handler)
    monkeypatch.setattr(ThreadingHTTPServer,"__init__",ephemeral)
    service=H.StudyHTTPServer(("127.0.0.1",9001),D.app)
    thread=threading.Thread(target=service.serve_forever,kwargs={"poll_interval":.005},daemon=True)
    thread.start()
    yield service
    service.shutdown()
    service.server_close()
    thread.join(.5)


def connect(server):
    client=socket.socket()
    client.settimeout(.275)
    client.connect(server.server_address)
    return client


def receive(client):
    parts=[]
    while True:
        data=client.recv(16384)
        if not data: return b"".join(parts)
        parts.append(data)


def test_actual_admission_before_thread_creation_and_headers_close(server):
    clients=[]
    try:
        start=time.monotonic()
        for _ in range(8):
            clients.append(connect(server))
        # One rejection slot is serviced synchronously by the accept loop.
        rejected=receive(clients[-1])
        assert b'503' in rejected and b'Live OFF' in rejected
        assert len(server._connections)<=7
        for client in clients[:-1]:
            try:
                assert client.recv(1)==b''
            except ConnectionResetError:
                pass
        assert time.monotonic()-start<.275
        assert not any(sock.fileno()!=-1 for sock in server._connections)
    finally:
        for client in clients: client.close()


def test_slow_drip_header_has_absolute_50ms_not_per_byte_timeout(server):
    client=connect(server)
    start=time.monotonic()
    try:
        client.sendall(b'GET /details HTTP/1.0\r\nX-Drip: ')
        for _ in range(9):
            threading.Event().wait(.01)
            try:
                client.sendall(b'x')
            except OSError:
                break
        try:
            assert client.recv(1)==b''
        except ConnectionResetError:
            pass
        assert time.monotonic()-start<.150
    finally:
        client.close()


def test_initial_html_api_errors_and_no_body_read(server):
    for path in ('/details','/twoyear-batch','/strategy-findings','/api/research/twoyear/summary','/not-a-route'):
        with connect(server) as client:
            start=time.monotonic()
            client.sendall(('GET '+path+' HTTP/1.0\r\n\r\n').encode())
            body=receive(client)
            assert b'Live OFF' in body and b'Connection: close' in body
            assert time.monotonic()-start<.275
    with connect(server) as client:
        client.sendall(b'POST /twoyear-batch HTTP/1.0\r\nContent-Length: 999999999\r\n\r\n')
        body=receive(client)
        assert b'405' in body and b'Live OFF' in body


def test_never_reading_client_cannot_hold_write_socket(server,monkeypatch):
    class Large:
        def response(self,*args,**kwargs):
            return V.BANNER.encode()+b'x'*(V.HTML_CAP-200),200,'text/html'
    monkeypatch.setattr(V,'STORE',Large())
    original=server.get_request
    def small_buffer():
        sock,address=original()
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
        return sock,address
    monkeypatch.setattr(server,'get_request',small_buffer)
    client=socket.socket()
    client.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
    client.settimeout(.275)
    client.connect(server.server_address)
    try:
        client.sendall(b'GET /twoyear-batch HTTP/1.0\r\n\r\n')
        threading.Event().wait(.275)
        assert not any(sock.fileno()!=-1 for sock in server._connections)
    finally:
        client.close()


def test_literal_bind_rejected():
    with pytest.raises(ValueError):
        H.StudyHTTPServer(('0.0.0.0',9001),D.app)
