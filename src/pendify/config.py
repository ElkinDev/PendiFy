"""The config file: the secret and the link id, under the user's profile (design P1, P6), and the page's theme
choice when there is one (light or dark; the system's choice is no key), and its language choice when there is one
(es or en; with none the page follows the browser).

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
# A refused os.replace is tried again after each pause: five retries over one second.
REPLACE_PAUSES = (0.2,) * 5
# The theme button's choices; "system" follows the system and is kept as no key.
THEMES = ("light", "dark")
SYSTEM_THEME = "system"
# The language switch's choices; with no key the page follows the browser's language.
LANGS = ("es", "en")
# _write's default: the stored theme and language choices are kept.
_KEEP = object()


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
        normalize. A malformed link id beside a good secret reads as no link id. Never writes. A file
        that is there and cannot be read raises ConfigError, never a first load over the stored pair."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise self._unavailable() from None
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

    def read_theme(self):
        """The stored theme choice, light or dark, or None when there is none (the system's). A file that is
        there and cannot be read raises ConfigError, as read() does; a missing, corrupt or foreign value is None."""
        return _choice(self._stored(), "theme", THEMES)

    def read_lang(self):
        """The stored language choice, es or en, or None when there is none (the browser's). A file that is there
        and cannot be read raises ConfigError, as read_theme() does; a missing, corrupt or foreign value is None."""
        return _choice(self._stored(), "lang", LANGS)

    def set_theme(self, choice):
        """The theme button's choice written beside the pair: light or dark kept, system kept as no key. Any
        other value is refused and nothing is written. Answers the stored choice, None for the system's."""
        if choice not in THEMES + (SYSTEM_THEME,):
            raise ValueError("the theme is not light, dark or system")
        theme = None if choice == SYSTEM_THEME else choice
        with self._lock:
            self._write(self._current(), theme=theme)
            return theme

    def set_lang(self, choice):
        """The language switch's choice, es or en, written beside the pair and the theme. Any other value is
        refused and nothing is written. Answers the stored choice."""
        if choice not in LANGS:
            raise ValueError("the language is not es or en")
        with self._lock:
            self._write(self._current(), lang=choice)
            return choice

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
        """The stored pair whose secret a write keeps; a file gone mid-run is the one sentence, as a held one is."""
        current = self.read()
        if current is None:
            raise self._unavailable()
        return current

    def _unavailable(self):
        return ConfigError(UNAVAILABLE.format(path=self.path))

    def _stored(self):
        """The file's object, or None when the file is missing, corrupt or not an object. A file that is there and
        cannot be read raises ConfigError."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise self._unavailable() from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:  # JSONDecodeError and UnicodeDecodeError alike
            return None
        return data if isinstance(data, dict) else None

    def _write(self, pairing, theme=_KEEP, lang=_KEEP):
        """The pair, and the theme and language choices: each stored one kept unless set_theme or set_lang passes
        its own."""
        folder = self.path.parent
        if theme is _KEEP or lang is _KEEP:
            stored = self._stored()
            theme = _choice(stored, "theme", THEMES) if theme is _KEEP else theme
            lang = _choice(stored, "lang", LANGS) if lang is _KEEP else lang
        values = {"secret": pairing.secret, "linkId": pairing.link_id}
        if theme is not None:
            values["theme"] = theme
        if lang is not None:
            values["lang"] = lang
        data = json.dumps(values).encode("utf-8")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(dir=folder, prefix=".config-", suffix=".tmp")
        except OSError:
            raise self._unavailable() from None
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            self._replace(temp)
        except BaseException as failure:
            if os.path.exists(temp):
                os.remove(temp)
            if isinstance(failure, OSError):
                raise self._unavailable() from None
            raise
        return pairing

    def _replace(self, temp):
        """os.replace, retried while Windows refuses it because another program holds the file open."""
        for pause in REPLACE_PAUSES:
            try:
                return os.replace(temp, self.path)
            except PermissionError:
                self._pause(pause)
        return os.replace(temp, self.path)


def _choice(stored, key, allowed):
    """The value of `key` in the file's object when it is one of `allowed`, else None (missing, corrupt or foreign)."""
    value = stored.get(key) if stored is not None else None
    return value if isinstance(value, str) and value in allowed else None
