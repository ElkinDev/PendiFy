"""The loopback pairing page."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORDS = {"es": {}, "en": {}}


class PairingPage:
    def __init__(self, state):
        self.state = state
        self.token = ""

    def start(self):
        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = do_PUT = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return ""

    def close(self):
        self.server.shutdown()
        self.server.server_close()
