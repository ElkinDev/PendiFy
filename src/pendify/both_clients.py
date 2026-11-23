"""The League client and the TFT app watched together on macOS, as one client for the watcher.

Each turn both are read and the one further along is followed: a pop first, then a match, then a queue or a champion
select, a lobby or nothing last. A pop on the League client is accepted by its API, one on the app by the click
(tft_app.AppSession).
"""
import json

from . import client

# How far along a source is, to follow the one where the queue was launched: a pop first, then a match, then a
# queue or a champion select; a lobby or nothing last.
_RANK = {"ReadyCheck": 4, "InProgress": 3, "Reconnect": 3, "ChampSelect": 2, "Matchmaking": 2,
         "GameStart": 3, "WaitingForStats": 1, "PreEndOfGame": 1, "EndOfGame": 1}


def league_phase(found, get=client.get, addresses=client.real_addresses):
    """The League client's phase through its API, or None when it gives none."""
    try:
        status, raw = get(addresses(found.port) + client.PHASE_PATH, found.token, client.CLIENT_TIMEOUT)
    except client.ClientUnreachable:
        return None
    if status != 200:
        return None
    try:
        phase = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return phase if isinstance(phase, str) else None


class BothClients:
    """The League client and the TFT app watched together, as one client for the watcher: each turn both are read,
    and the one further along (a pop, a match, a queue) is followed, so a queue launched in either is seen with the
    other open. On the League client a pop is accepted by its API (`accepts`, `port`, `token` are its own); on the
    app, by the click. Lost only when neither answers."""

    def __init__(self, league, app, get=client.get, addresses=client.real_addresses):
        self._league, self._app, self._get, self._addresses = league, app, get, addresses
        self._found = None  # the League client's credentials while it answers
        self.active = None  # "league" or "app": the one the last phase came from

    @property
    def accepts(self):
        return self.active == "league"

    @property
    def port(self):
        return self._found.port if self._found is not None else 0

    @property
    def token(self):
        return self._found.token if self._found is not None else None

    @property
    def pop(self):
        """The app's pop stamp while the app is followed (AppSession.pop); a League client's ready check has none."""
        return self._app.pop if self.active == "app" else None

    def present(self):
        """True while either answers: the League client's credentials or the app's process."""
        return self._league.read() is not None or self._app.running()

    def phase(self):
        self._found = self._league.read()
        league = league_phase(self._found, self._get, self._addresses) if self._found is not None else None
        try:
            app = self._app.phase()
        except client.ClientUnreachable:
            app = None
        if league is None and app is None:
            self._found, self.active = None, None
            raise client.ClientUnreachable("none")
        if app is not None and (league is None or _RANK.get(app, 0) > _RANK.get(league, 0)):
            self.active = "app"
            return app
        self.active = "league"
        return league

    def auto_accept_on(self):
        return self.active == "app" and self._app.auto_accept_on()

    def auto_accept(self):
        return self._app.auto_accept()


class EitherClient:
    """The watcher's credentials: BothClients while the League client or the TFT app answers."""

    def __init__(self, league, app, get=client.get, addresses=client.real_addresses):
        self._both = BothClients(league, app, get, addresses)

    def read(self):
        return self._both if self._both.present() else None
