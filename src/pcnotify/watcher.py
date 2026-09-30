"""Inert seam: WatcherTest is committed before the watcher."""
ALERT_PHASES = ()
ACCEPT_DELAY = (0.0, 0.0)
STEP_PAUSE = NO_CLIENT_PAUSE = 0.0
CONNECTED_LINE = ACCEPTED_LINE = LOST_LINE = ""


def accept_delay():
    return 0.0


class Watcher:
    def __init__(self, credentials, alert, **kwargs):
        pass

    def step(self):
        return 0.0

    def run(self):
        pass

    def snapshot(self):
        return {"client": None, "alert": None, "at": None}
