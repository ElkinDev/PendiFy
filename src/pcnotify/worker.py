"""The Worker's two unauthenticated link routes, check (design P5) and ping (S:282-308), on urllib only.

Nothing either function prints, logs or raises carries the secret or the link id: failures are named by
an HTTP status or an exception type, results hide their values from repr, and a refused input is refused
with a fixed sentence before any call.
"""
import importlib.metadata
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import codes

# The host of the ping address the owner's script uses (S:84).
BASE_URL = "https://followapp-ai-proxy.niklerk23.workers.dev"
CHECK_PATH = "/v1/link-check"
PING_PATH = "/v1/link-ping"
TIMEOUT_SECONDS = 5
# The two kinds this program sends, the contract with the Worker; the only place they are written.
KINDS = ("lol_queue_found", "lol_match_started")
_MAX_ANSWER_BYTES = 64 * 1024
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def _user_agent():
    try:
        return f"pcnotify/{importlib.metadata.version('pcnotify')}"
    except importlib.metadata.PackageNotFoundError:  # a source-tree run: the tests, a checkout
        return "pcnotify/source"


# The program names itself in every request: the edge in front of the Worker refuses urllib's default
# signature (Python-urllib/<version>) with error 1010 before the Worker reads the request.
USER_AGENT = _user_agent()


@dataclass(frozen=True)
class Linked:
    link_id: str = field(repr=False)


@dataclass(frozen=True)
class Refused:
    pass


@dataclass(frozen=True)
class Throttled:
    pass


@dataclass(frozen=True)
class Failed:
    reason: str


@dataclass(frozen=True)
class Sent:
    pass


@dataclass(frozen=True)
class NotDelivered:
    status: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect answer is returned as it is (an HTTPError), never followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_base(value):
    """A replacement Worker address for test runs: only http://127.0.0.1:<port> or http://localhost:<port>."""
    parts = urllib.parse.urlsplit(value) if isinstance(value, str) else None
    try:
        port = parts.port if parts else None
    except ValueError:
        port = None
    if (parts is None or parts.scheme != "http" or parts.hostname not in _LOOPBACK_HOSTS or port is None
            or parts.username is not None or parts.path not in ("", "/") or parts.query or parts.fragment
            or parts.netloc != f"{parts.hostname}:{port}"):
        raise ValueError("a replacement Worker address must be http://127.0.0.1:<port> or http://localhost:<port>")
    return f"http://{parts.hostname}:{port}"


def _post(base, path, payload, timeout):
    """(status, answer dict or None), or a Failed naming the exception type."""
    request = urllib.request.Request(
        base + path, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT})
    handlers = [_NoRedirect()]
    if urllib.parse.urlsplit(base).hostname in _LOOPBACK_HOSTS:
        handlers.append(urllib.request.ProxyHandler({}))  # a loopback address never goes through a proxy
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read(_MAX_ANSWER_BYTES)
    except urllib.error.HTTPError as answer:
        with answer:
            status, raw = answer.code, answer.read(_MAX_ANSWER_BYTES)
    except urllib.error.URLError as failure:
        reason = failure.reason
        return Failed(type(reason).__name__ if isinstance(reason, BaseException) else type(failure).__name__)
    except (OSError, ValueError) as failure:  # a timeout while reading, a reset, a malformed answer
        return Failed(type(failure).__name__)
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        body = None
    return status, body if isinstance(body, dict) else None


def _error_code(body):
    error = body.get("error") if body else None
    return error.get("code") if isinstance(error, dict) else None


def check(secret, *, base=BASE_URL, timeout=TIMEOUT_SECONDS):
    """POST {secret} to the link check: Linked(link id), Refused, Throttled or Failed."""
    normal = codes.normalize(secret)
    if normal is None:
        raise ValueError("the secret is not twelve symbols")
    answer = _post(base, CHECK_PATH, {"secret": normal}, timeout)
    if isinstance(answer, Failed):
        return answer
    status, body = answer
    link_id = body.get("linkId") if body else None
    if status == 200 and isinstance(link_id, str) and codes.normalize(link_id) == link_id:
        return Linked(link_id)
    code = _error_code(body)
    if code == "link_refused":
        return Refused()
    if code == "throttled":
        return Throttled()
    return Failed(f"HTTP {status}")


def ping(link_id, secret, kind, *, base=BASE_URL, timeout=TIMEOUT_SECONDS):
    """POST {linkId, secret, kind} to the link ping: Sent, Refused, NotDelivered(status) or Failed."""
    if kind not in KINDS:
        raise ValueError("the kind is not one of the two this program sends")
    normal_link_id, normal_secret = codes.normalize(link_id), codes.normalize(secret)
    if normal_link_id is None or normal_secret is None:
        raise ValueError("the stored pair is not two values of twelve symbols")
    answer = _post(base, PING_PATH, {"linkId": normal_link_id, "secret": normal_secret, "kind": kind}, timeout)
    if isinstance(answer, Failed):
        return answer
    status, body = answer
    if 200 <= status < 400 and body and body.get("sent"):
        return Sent()
    if _error_code(body) == "link_refused":
        return Refused()
    return NotDelivered(status)
