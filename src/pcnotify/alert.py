"""Inert seam: WatcherTest is committed before the alert."""
PING_LINES = {"sent": "", "refused": "", "not_delivered": "", "failed": ""}


def beep(sound=None, start=None):
    return None


class Alerter:
    def __init__(self, store, state, ping, **kwargs):
        pass

    def __call__(self, kind=None):
        pass

    def flush(self, timeout):
        return False
