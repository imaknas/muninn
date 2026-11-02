"""Where a program Muninn runs (tailscale, uv, a model's CLI) is found. A service manager starts Muninn with a PATH
of its own (launchd's is /usr/bin:/bin:/usr/sbin:/sbin); the installer passes on the PATH it ran with, but a program
installed after that, where the user's shell finds it, is looked for in those places too."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable

USUAL = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")  # uv's and Claude's installers, Homebrew


def find_program(name: str, places: Iterable[str] = USUAL) -> str | None:
    if found := shutil.which(name):
        return found
    for place in places:
        path = Path(place).expanduser() / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None
