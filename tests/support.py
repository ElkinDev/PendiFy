"""Shared test support.

The package under test is found as the one package directory under src/, so its working name is
written nowhere in the tests. Temporary folders live under build/tmp inside the repository, and the
fake Worker listens on 127.0.0.1 only.
"""
import importlib
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BUILD = ROOT / "build"

# Test vectors, never a real pairing: SECRET is the Worker's own (proxy/test/link.test.ts:874).
SECRET = "ABCD2345EFGH"
LINK_ID = "WXYZ6789ABCD"

# The longest a held request of the fake Worker stays open before it is let go.
HOLD_LIMIT = 3.0


def _package_name():
    names = sorted(p.name for p in SRC.iterdir() if (p / "__init__.py").is_file())
    if len(names) != 1:
        raise RuntimeError(f"expected exactly one package under {SRC}, found {names}")
    return names[0]


PACKAGE = _package_name()
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def module(name):
    """The package's module `name`, imported by the package's discovered name."""
    return importlib.import_module(f"{PACKAGE}.{name}")


def package_dir():
    return SRC / PACKAGE


def temp_dir():
    """A TemporaryDirectory under build/tmp, so no test writes outside the repository."""
    base = BUILD / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class FakeWorker:
    """The Worker's link routes on 127.0.0.1, in the shape of l4/test_pendi_pairing.py:79-124.

    answer(path, body) -> (status, payload) or (status, payload, headers), or None to hold the
    request without an answer until close(). Every request is recorded with its path and raw body.
    """

    def __init__(self, answer):
        self.answer, self.requests, self.release = answer, [], threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _record(self, body):
                fake.requests.append({"method": self.command, "path": self.path, "body": body,
                                      "headers": {k.lower(): v for k, v in self.headers.items()}})

            def do_GET(self):
                self._record(b"")
                self._reply((200, {"followed": True}))

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self._record(body)
                reply = fake.answer(self.path, body)
                if reply is None:
                    fake.release.wait(HOLD_LIMIT)
                    return
                self._reply(reply)

            def _reply(self, reply):
                status, payload, extra = (tuple(reply) + ({},))[:3]
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for key, value in extra.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        # A short poll interval, so close() does not wait half a second per fake.
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def bodies(self):
        return [json.loads(r["body"]) for r in self.requests if r["method"] == "POST"]

    def wait_for(self, count, limit=2.0):
        deadline = time.monotonic() + limit
        while len(self.requests) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        return list(self.requests)

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def error(code, status):
    """A Worker error body as errors.ts shapes it."""
    return (status, {"error": {"code": code, "message": code}})


class FakeClock:
    """A clock the test moves by hand; nothing sleeps."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
