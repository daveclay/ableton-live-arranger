"""Non-blocking, newline-delimited JSON TCP server.

Live's embedded Python can't run threads safely, so the Manager calls
`process()` from Live's main-thread tick. Each request is one JSON object per
line: {"id": ..., "cmd": "...", "args": {...}}. Each response is one line:
{"id": ..., "ok": true, "result": ...} or {"id": ..., "ok": false, "error": "..."}.
"""

import json
import socket

HOST = "127.0.0.1"  # localhost only
PORT = 11020


class Server:
    def __init__(self, dispatch, log):
        self._dispatch = dispatch
        self._log = log
        self._clients = {}  # socket -> bytearray (read buffer)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((HOST, PORT))
        self._sock.listen(4)
        self._sock.setblocking(False)

    def process(self):
        self._accept()
        for client in list(self._clients):
            self._read(client)

    def _accept(self):
        while True:
            try:
                client, _ = self._sock.accept()
            except (BlockingIOError, InterruptedError):
                return
            client.setblocking(False)
            self._clients[client] = bytearray()

    def _read(self, client):
        buf = self._clients[client]
        while True:
            try:
                chunk = client.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self._drop(client)
                return
            if not chunk:
                self._drop(client)
                return
            buf.extend(chunk)
        while b"\n" in buf:
            line, _, rest = bytes(buf).partition(b"\n")
            buf[:] = rest
            if line.strip():
                self._handle(client, line)

    def _handle(self, client, line):
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            result = self._dispatch(req["cmd"], req.get("args") or {})
            resp = {"id": req_id, "ok": True, "result": result}
        except Exception as e:  # report every failure back to the client
            import traceback
            resp = {"id": req_id, "ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "traceback": traceback.format_exc()}
        data = (json.dumps(resp, default=repr) + "\n").encode()
        try:
            client.setblocking(True)  # responses are small; simplest to send fully
            client.sendall(data)
            client.setblocking(False)
        except OSError:
            self._drop(client)

    def _drop(self, client):
        self._clients.pop(client, None)
        try:
            client.close()
        except OSError:
            pass

    def shutdown(self):
        for client in list(self._clients):
            self._drop(client)
        self._sock.close()
