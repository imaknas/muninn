"""Runtime configuration: <data>/config.yaml over built-in defaults, plus env.

MUNINN_DATA        data directory (default ./data)
MUNINN_PUBLIC_URL  the tailnet URL the PWA is served at (also the VAPID subject)
MUNINN_HOST        the address to listen on, over config `host` (a container listens on 0.0.0.0)
MUNINN_SECRETS_DIR where Muninn's own keys live (or config secrets_dir; relative to the data
                   directory; default <data>/secrets)

A credential that is missing is an error, not something quietly made again or
done without. No path on one particular machine is written here: the Claude
token file is named in config.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from muninn.core.handoff_records import parties_from
from muninn.languages import CODES, system_language

DEFAULTS: dict[str, Any] = {
    "timezone": None,  # an IANA name; unset: the machine's own (UTC when it cannot be told)
    "language": None,  # what Muninn speaks: zh-TW or en (muninn/languages); unset: the machine's own (English otherwise)
    "host": "127.0.0.1",
    "port": 18795,
    "quiet_hours": "23:00-08:00",
    "active_hours": "08:00-23:00",
    "late_night": "00:30-05:00",
    "daily_push_cap": 8,
    "report_times": "08:30 · 22:30",  # shown in the PWA; the scheduler owns the real times
    # the built-in schedule (adapters/scheduler.py): "default", or job → time; unset, another scheduler runs the jobs
    "jobs": None,
    "documents": [],  # the user's resume versions etc., for preparing applications
    "cloud_drafts": False,  # allow the cloud model to draft from those documents and public postings
    "prepare_per_tick": 1,
    "reflect": True,  # read conversation text (sensitive, so as routing allows) for the weekly reflection
    "calendar_name": "Muninn",  # the calendar Muninn may write to (created by the user)
    # the mailboxes to read, each {name, kind: personal | work, provider: gmail | outlook}; one is read once its
    # provider's client is set up
    "mail_accounts": [],
    "owner_label": "owner",  # how the records system names the user in its notes (「〔<label>〕」, 「等 <label>:<key>」)
    # the user's identifier in Principal Handoff records (the principal, configured on both sides); unset: made from
    # owner_label as "person:<label>"
    "principal_id": None,
    "cloud_mail": False,  # personal mail may be read by the cloud model; work mail never is
    # lending a conversation to a project's session: the session runner's local URL and the file holding its
    # Bearer token; both unset = the feature is off
    "lease_runner_url": None,
    "lease_runner_token_file": None,
    # where the projects live: "external" (another system keeps them; it posts to /api/messages and pulls
    # the handoffs) or "local" (plain folders: only those listed, plus sandboxes opened for ideas)
    "projects": {"side": "local", "folders": [], "sandbox_root": None},
    # what the user types (commands, facts, questions) may be read by the cloud model once a
    # local work check clears it; work stays local. The model is llm.understanding_model.
    "cloud_understanding": False,
    "idle_alert_days": 3,
    "workspace_roots": [],  # folders whose git repositories count as the user's work
    "transcript_roots": [],  # folders of Claude Code conversations (e.g. ~/.claude/projects)
    "git_authors": [],
    "git_exclude": [],
    # budget.daily_usd: what the cloud models may cost in a day (US dollars, at the prices llm.providers.<p>.prices
    # gives); once reached, no cloud model is asked until the next day, and the user is told. Unset: no limit
    "budget": {"daily_usd": None},
    "llm": {
        "local_model": None,  # an Ollama model on this machine (e.g. qwen3:8b); unset: no local model
        "ollama_url": "http://127.0.0.1:11434",
        # providers / use (llm_config.py): the cloud providers, each with its key's place and models; left out, the keys below
        # name the one cloud the older way
        "cloud": "none",  # "claude-cli" for the Claude CLI; "none": no cloud model unless providers names one
        "routing": "privacy",  # "cloud": every request to the cloud model, local only when it fails
        # for cloud "claude-cli": a name on PATH or a path; not found there, the newest node under nvm that has it
        "claude_binary": "claude",
        # for cloud "claude-cli", how the CLI logs in: the file holding the token `claude setup-token` writes (read on
        # every call); unset, the CLI's own login
        "claude_token_file": None,
        "cloud_model": None,  # unset: the cloud provider's own default
        "understanding_model": None,  # for the user's own words, when cloud_understanding is on; unset: the provider's
    },
}


def _window(s: str) -> tuple[time, time]:
    a, b = s.split("-")
    return time.fromisoformat(a.strip()), time.fromisoformat(b.strip())


def _deep_merge(base: dict[str, Any], over: dict[str, Any], where: str = "") -> dict[str, Any]:
    """The defaults with config over them; a section config empties (`llm:` alone) keeps its defaults, and one that is
    not a section at all is said, not found out later."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(base.get(k), dict):
            if v is None:
                continue
            if not isinstance(v, dict):
                raise BadSetting(f"{where}{k}: a section of settings, not {v!r}")
            out[k] = _deep_merge(base[k], v, f"{where}{k}.")
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    """A settings file; one that does not read as settings is said with where it goes wrong (BadSetting)."""
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path.read_text())
    except YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        at = f" at line {mark.line + 1}" if mark is not None else ""
        raise BadSetting(f"{path} is not valid YAML{at}: {getattr(e, 'problem', None) or e}") from None
    if data is not None and not isinstance(data, dict):
        raise BadSetting(f"{path} should hold settings (name: value), not {type(data).__name__}")
    return dict(data or {})


def installed_data_dir(environ: Mapping[str, str] = os.environ, home: Path | None = None, platform: str = sys.platform) -> Path:
    """The data folder install.sh makes (kept the same as there)."""
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Muninn"
    return Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "muninn"


def default_data_dir(environ: Mapping[str, str] = os.environ, cwd: Path | None = None, home: Path | None = None,
                     platform: str = sys.platform) -> Path:
    """Where Muninn's data is when no --data says: $MUNINN_DATA; else ./data where a checkout keeps it; else the
    folder the installer made, so `muninn pair` and the rest find the Muninn that is running; else ./data, made new."""
    if environ.get("MUNINN_DATA"):
        return Path(environ["MUNINN_DATA"])
    here = (cwd or Path.cwd()) / "data"
    if (here / "config.yaml").is_file() or (here / "muninn.db").is_file():
        return here
    installed = installed_data_dir(environ, home, platform)
    return installed if (installed / "config.yaml").is_file() else here


def system_timezone(environ: Mapping[str, str] = os.environ, localtime: Path = Path("/etc/localtime"),
                    named: Path = Path("/etc/timezone")) -> str:
    """The machine's time zone as an IANA name: $TZ, where /etc/localtime points, or /etc/timezone; UTC when
    none of them names one."""
    candidates = [environ.get("TZ", "").lstrip(":")]
    try:
        target = str(localtime.resolve()) if localtime.is_symlink() else ""
        candidates.append(target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else "")
    except OSError:
        pass
    try:
        candidates.append(named.read_text().strip())
    except OSError:
        pass
    for name in candidates:
        try:
            if name:
                ZoneInfo(name)
                return name
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC"


class BadSetting(ValueError):
    """A setting in config.yaml that cannot be what it says."""


class MissingSecret(RuntimeError):
    """A credential Muninn needs is not where it was said to be, or where it lives was never said."""


@dataclass(frozen=True)
class Config:
    data_dir: Path
    raw: dict[str, Any]
    attribution: dict[str, Any] = field(default_factory=dict)
    public_url: str | None = None

    @classmethod
    def load(cls, data_dir: str | os.PathLike[str] | None = None) -> Config:
        d = Path(data_dir or default_data_dir()).expanduser().resolve()
        raw = _deep_merge(DEFAULTS, _load_yaml(d / "config.yaml"))
        if os.environ.get("MUNINN_HOST"):
            raw["host"] = os.environ["MUNINN_HOST"]
        return cls(
            data_dir=d,
            raw=raw,
            attribution=_load_yaml(d / "attribution.yaml"),
            public_url=os.environ.get("MUNINN_PUBLIC_URL") or raw.get("public_url"),
        )

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def effective(self) -> dict[str, Any]:
        """The settings as this installation runs with them: config.yaml over the defaults, with what is worked
        out from them (time zone, principal, where the data and keys are) filled in. Keys appear only as where
        they are; config never holds one."""
        out = dict(self.raw)
        out["timezone"] = self.tz
        out["language"] = self.language
        out["port"] = self.port
        out["principal_id"] = parties_from(self.raw.get("principal_id"), str(self.raw["owner_label"])).principal
        out["data_dir"] = str(self.data_dir)
        out["secrets_dir"] = str(self.secrets_dir)
        out["public_url"] = self.public_url
        return out

    @property
    def tz(self) -> str:
        return self.raw["timezone"] or system_timezone()

    @property
    def language(self) -> str:
        code = str(self.raw["language"] or system_language())
        if code not in CODES:
            raise BadSetting(f"language: one of {', '.join(CODES)} (config says {code!r})")
        return code

    @property
    def port(self) -> int:
        """The port Muninn listens on, on this machine."""
        port = self.raw["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise BadSetting(f"port: a number from 1 to 65535 (config says {port!r})")
        return port

    @property
    def quiet_hours(self) -> tuple[time, time]:
        return _window(self.raw["quiet_hours"])

    @property
    def active_hours(self) -> tuple[time, time]:
        return _window(self.raw["active_hours"])

    @property
    def late_night(self) -> tuple[time, time]:
        return _window(self.raw["late_night"])

    def paths(self, key: str) -> list[Path]:
        return [Path(p).expanduser() for p in self.raw[key]]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "muninn.db"

    @property
    def domains_dir(self) -> Path:
        return self.data_dir / "domains"

    @property
    def secrets_dir(self) -> Path:
        where = os.environ.get("MUNINN_SECRETS_DIR") or self.raw.get("secrets_dir") or "secrets"
        path = Path(str(where)).expanduser()
        return path if path.is_absolute() else self.data_dir / path

    @property
    def vapid_subject(self) -> str:
        """VAPID `sub`: a mailto: or an https URL with no port or path (push
        services and the VAPID library reject anything else)."""
        if self.raw.get("vapid_subject"):
            return self.raw["vapid_subject"]
        if self.public_url:
            host = urlsplit(self.public_url).hostname
            if host:
                return f"https://{host}"
        return "mailto:muninn@localhost"

    def git_authors(self) -> list[str]:
        if self.raw["git_authors"]:
            return list(self.raw["git_authors"])
        try:
            email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            email = ""
        return [email] if email else []

    def ingest_token(self, *, create: bool = False) -> str:
        """Bearer token for machine-to-machine ingest. Only `muninn init` creates it: made again
        anywhere else, the phone's shortcuts and location app would quietly stop getting in."""
        path = self.secrets_dir / "ingest_token"
        if not path.exists():
            if not create:
                raise MissingSecret(f"no ingest token at {path} (muninn init creates one)")
            self.secrets_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.secrets_dir, 0o700)
            path.write_text(secrets.token_urlsafe(32))
            os.chmod(path, 0o600)
        return path.read_text().strip()
