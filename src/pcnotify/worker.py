"""The Worker's two unauthenticated link routes: check and ping."""
from dataclasses import dataclass

BASE_URL = ""
TIMEOUT_SECONDS = 0
KINDS = ()


@dataclass(frozen=True)
class Linked:
    link_id: str


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


def loopback_base(value):
    return value


def check(secret, *, base=BASE_URL, timeout=TIMEOUT_SECONDS):
    return None


def ping(link_id, secret, kind, *, base=BASE_URL, timeout=TIMEOUT_SECONDS):
    return None
