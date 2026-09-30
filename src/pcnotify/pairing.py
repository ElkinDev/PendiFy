"""The pairing state of design P5."""
import time

CHECK_INTERVAL = 0.0
CHECK_CAP = 0
RATE_LIMIT = 0
RATE_WINDOW = 0.0
PAGE_OPEN_WINDOW = 70.0


class PairingState:
    def __init__(self, store, check, clock=time.monotonic):
        self._store = store
        store.load()

    def page_seen(self):
        pass

    def tick(self):
        return None

    def ask(self):
        return None

    def record_ping(self, result):
        pass

    def accept_relink(self):
        return None

    def forget(self):
        pass

    def typed(self, link_id, secret):
        return None

    def secret_for_display(self):
        return ""

    def snapshot(self):
        return {"state": "", "linked": None, "showCode": None, "relinkOffered": None,
                "buttonRefused": None, "typedRefused": None}
