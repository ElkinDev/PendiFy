"""The config file: exactly the secret and the link id, under the user's profile."""
from dataclasses import dataclass, field
from pathlib import Path

FOLDER_NAME = ""
FILE_NAME = "config.json"


@dataclass(frozen=True)
class Pairing:
    secret: str = ""
    link_id: object = None


def default_base_dir(environ):
    return Path(".")


class ConfigStore:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / FILE_NAME

    def read(self):
        return Pairing()

    def load(self):
        return Pairing()

    def forget(self):
        return Pairing()

    def set_typed(self, link_id, secret):
        return Pairing()

    def set_link_id(self, link_id):
        return Pairing()

    def clear_link_id(self):
        return Pairing()
