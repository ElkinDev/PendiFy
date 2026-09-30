"""The game client's side. Inert seam: the pins of ClientCredentialsTest are committed before the code."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Credentials:
    port: int
    token: str = field(repr=False)


class ClientCredentials:
    def __init__(self, paths=(), run=None, clock=None):
        self._paths, self._run, self._clock = paths, run, clock

    def read(self):
        return None
