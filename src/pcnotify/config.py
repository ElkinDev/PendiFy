"""The config file: exactly the secret and the link id, under the user's profile (design P1, P6).

The file is `<base>/<folder>/config.json`, where the base is %APPDATA% in a real run and is injected
everywhere else, so no test touches the real profile. A write goes to a temp file in the same folder
and is moved over the old one with os.replace, so a reader sees the old file or the new one, never half.
"""
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import codes

# The folder under the base is named after the package, the one constant that names it.
FOLDER_NAME = __name__.split(".")[0]
FILE_NAME = "config.json"
# The one sentence for a config file that is there but cannot be read or replaced, most often because
# another program holds it open. It names the path, never anything the file holds.
UNAVAILABLE = "the config file {path} cannot be used now: close any program that holds it open and start again"


class ConfigError(OSError):
    """The config file is there but cannot be read or written; the message is UNAVAILABLE."""


@dataclass(frozen=True)
class Pairing:
    """What the file holds. Neither value is ever shown by repr."""

    secret: str = field(repr=False)
    link_id: Optional[str] = field(repr=False, default=None)


def default_base_dir(environ=None):
    """%APPDATA%, the base of a real run. Its absence is an error, never a silent fallback."""
    environ = os.environ if environ is None else environ
    value = (environ.get("APPDATA") or "").strip()
    if not value:
        raise RuntimeError("APPDATA is not set, so the config folder has no place")
    return Path(value)


class ConfigStore:
    def __init__(self, base_dir, *, pause=time.sleep):
        self.path = Path(base_dir) / FOLDER_NAME / FILE_NAME
        self._lock = threading.Lock()
        self._pause = pause

    def read(self):
        """The stored pair, or None when the file is missing, empty, corrupt or its secret does not
        normalize. A malformed link id beside a good secret reads as no link id. Never writes."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:  # JSONDecodeError and UnicodeDecodeError alike
            return None
        if not isinstance(data, dict):
            return None
        secret = codes.normalize(data.get("secret"))
        if secret is None:
            return None
        return Pairing(secret, codes.normalize(data.get("linkId")))

    def load(self):
        """The stored pair; when there is none, a first load: a new secret, written (P1)."""
        with self._lock:
            current = self.read()
            if current is None:
                current = self._write(Pairing(codes.mint()))
            return current

    def forget(self):
        """«Olvidar este PC»: a new secret and no link id."""
        with self._lock:
            old = self.read()
            secret = codes.mint()
            while old is not None and secret == old.secret:
                secret = codes.mint()
            return self._write(Pairing(secret))

    def set_typed(self, link_id, secret):
        """The typed road (P6): both values normalized, a malformed pair refused without echoing it."""
        normal_link_id, normal_secret = codes.normalize(link_id), codes.normalize(secret)
        if normal_link_id is None or normal_secret is None:
            raise ValueError("the typed pair is not two values of twelve symbols")
        with self._lock:
            return self._write(Pairing(normal_secret, normal_link_id))

    def set_link_id(self, link_id):
        """The link id a check answered, stored beside the current secret."""
        normal = codes.normalize(link_id)
        if normal is None:
            raise ValueError("the link id is not twelve symbols")
        with self._lock:
            return self._write(Pairing(self._current().secret, normal))

    def clear_link_id(self):
        """«Volver a enlazar»: no link id, the SAME secret."""
        with self._lock:
            return self._write(Pairing(self._current().secret))

    def _current(self):
        current = self.read()
        if current is None:
            raise RuntimeError("the config file is missing or unreadable")
        return current

    def _write(self, pairing):
        folder = self.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"secret": pairing.secret, "linkId": pairing.link_id}).encode("utf-8")
        handle, temp = tempfile.mkstemp(dir=folder, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise
        return pairing
