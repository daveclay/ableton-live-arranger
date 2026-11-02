"""Client for the LiveArranger Remote Script (JSON lines over TCP)."""

import itertools
import json
import socket
import time

HOST, PORT = "127.0.0.1", 11020


class LiveError(Exception):
    def __init__(self, message, traceback=None):
        super().__init__(message)
        self.remote_traceback = traceback


class LiveClient:
    def __init__(self, host=HOST, port=PORT, timeout=10.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._file = self._sock.makefile("rb")
        self._ids = itertools.count(1)

    def call(self, cmd, **args):
        req_id = next(self._ids)
        self._sock.sendall((json.dumps({"id": req_id, "cmd": cmd, "args": args}) + "\n").encode())
        line = self._file.readline()
        if not line:
            raise ConnectionError("LiveArranger closed the connection")
        resp = json.loads(line)
        if not resp["ok"]:
            raise LiveError(resp["error"], resp.get("traceback"))
        return resp["result"]

    def run_job(self, cmd, timeout=60.0, **args):
        """Start a job-returning command and wait for it to finish."""
        job = self.call(cmd, **args)["job"]
        deadline = time.monotonic() + timeout
        while True:
            st = self.call("job_status", job=job)
            if st["status"] == "done":
                return st["result"]
            if st["status"] == "failed":
                raise LiveError(st["error"], st.get("traceback"))
            if time.monotonic() > deadline:
                raise TimeoutError("job %s still running after %ss" % (job, timeout))
            time.sleep(0.05)

    def reload(self):
        return self.call("reload")

    def close(self):
        self._file.close()
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
