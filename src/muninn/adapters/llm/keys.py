"""Where an API provider's key is: a file in Muninn's secrets directory, or an environment variable.

The key is read on every request and kept nowhere else, so a key put in place (or replaced) works at once.
A key that is not there is a lapsed login (CloudLoginLost): the user hears of it, and no other model answers
in its place.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Protocol

from muninn.core.language import Language
from muninn.core.ports import CloudLoginLost


class KeySource(Protocol):
    where: str  # what the user is told when the key is missing ("" for none)

    def read(self) -> str:
        """The key; CloudLoginLost when there is none."""
        ...


class SecretFile:
    def __init__(self, path: Path):
        self._path = Path(path)
        self.where = str(self._path)

    def read(self) -> str:
        try:
            key = self._path.read_text().strip()
        except OSError:
            key = ""
        if not key:
            raise CloudLoginLost(f"no API key in {self._path}")
        return key


class EnvKey:
    def __init__(self, name: str, environ: Mapping[str, str] = os.environ, *, words: Language):
        self._name = name
        self._environ = environ
        self.where = words("llm.env_var", name=name)

    def read(self) -> str:
        key = (self._environ.get(self._name) or "").strip()
        if not key:
            raise CloudLoginLost(f"no API key in ${self._name}")
        return key


def key_source(spec: dict[str, str], secrets_dir: Path, words: Language) -> KeySource:
    """`spec` as config writes it (checked by llm_config): {"secret": name} or {"env": NAME}."""
    return SecretFile(secrets_dir / spec["secret"]) if "secret" in spec else EnvKey(spec["env"], words=words)
