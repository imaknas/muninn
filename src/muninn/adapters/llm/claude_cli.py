"""Cloud backend: the `claude` CLI in print mode, sealed: no tools, no saved session.

Runs in an empty working directory so no project files or instructions are
picked up, and with session persistence off so it leaves no transcript behind
(which the Claude Code source would otherwise read as activity). Turning the
tools off does not turn off the user's MCP servers, hooks, plugins or memory:
those are shut out explicitly (`SEALED`), so a model call reads only what
Muninn gives it and can reach nothing else (a database, another service).

How it logs in is one of two credentials, chosen at the composition root: the
long-lived headless token (`claude setup-token`), read from its file on every
call and handed over in the environment, never kept or logged; or the CLI's
own login (the keychain), as it finds it. Where the CLI is gets looked up on
every call too, so a newer node under nvm is found once the old one is gone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from muninn.adapters.llm.content import content_blocks
from muninn.core.language import Language
from muninn.core.models import Attachment
from muninn.core.ports import Capabilities, CloudLoginLost
from muninn.llm_config import ProviderContext, ProviderSpec

# what the CLI prints when its login has lapsed (subscription or token)
LOGIN_PHRASES = r"login expired|please run /login|not logged in|invalid api key|oauth token has expired|invalid bearer token"
LOGIN_LOST = re.compile(rf"(?i){LOGIN_PHRASES}|\b401\b|authentication_error")  # a failed run's output
LOGIN_REPLY = re.compile(rf"(?i)^\W*(?:{LOGIN_PHRASES}|api error: 401\b)")  # printed as the reply itself (no bare 401: that may be an answer)


TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

# no MCP server but the ones given (none), no hooks, no memory read or written, nothing synced in
SEALED = ["--strict-mcp-config", "--settings", json.dumps({
    "disableAllHooks": True, "autoMemoryEnabled": False, "autoDreamEnabled": False,
    "syncClaudeAiPlugins": False, "syncClaudeAiSkills": False, "advisorModel": ""})]


class Credentials(Protocol):
    def env(self) -> dict[str, str]:
        """What the CLI's environment carries to log in; CloudLoginLost when that cannot be had."""
        ...


class HeadlessToken:
    """The token `claude setup-token` made (a year long), read from its file on every call."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def env(self) -> dict[str, str]:
        try:
            token = self._path.read_text().strip()
        except OSError:
            token = ""
        if not token:
            raise CloudLoginLost(f"no headless token in {self._path}")
        return {TOKEN_VAR: token}


class OwnLogin:
    """The CLI's own login (the keychain), whatever it finds there."""

    def env(self) -> dict[str, str]:
        return {}


def find_cli(configured: str, nvm_dir: Path | None = None, name: str = "claude") -> str | None:
    """A node CLI (`name`) as configured (a path, or a name on PATH); else the newest node under nvm that has it."""
    path = os.path.expanduser(configured)
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    if found := shutil.which(path):
        return found
    if nvm_dir is None:
        return None
    candidates = [(tuple(int(x) for x in re.findall(r"\d+", v.name)), v / "bin" / name)
                  for v in (nvm_dir / "versions" / "node").glob("v*")]
    return next((str(c) for _, c in sorted(candidates, reverse=True) if c.is_file() and os.access(c, os.X_OK)), None)


class ClaudeCliBackend:
    is_local = False
    label = "Claude"

    def __init__(self, workdir: Path, model: str = "haiku", binary: str = "claude", timeout: float = 120.0,
                 credentials: Credentials | None = None, nvm_dir: Path | None = None, scrub: Iterable[str] = (), *,
                 words: Language):
        """`binary`: as configured (see find_claude); `credentials`: how it logs in (by default its own
        login); `nvm_dir`: where to look for it when `binary` is not there; `scrub`: environment variables
        it is not handed."""
        self.login_hint = words("llm.claude_login")
        self.name = f"claude-cli:{model}"
        self._workdir = Path(workdir)
        self._model = model
        self._binary = binary
        self._timeout = timeout
        self._credentials = credentials or OwnLogin()
        self._nvm_dir = nvm_dir
        self._scrub = frozenset(scrub) | {TOKEN_VAR}

    def _cli(self) -> str:
        found = find_cli(self._binary, self._nvm_dir)
        if found is None:
            raise FileNotFoundError(f"claude CLI not found ({self._binary})")
        return found

    def _env(self, cli: str) -> dict[str, str]:
        """The CLI is a node script: its own directory (where node sits under nvm) goes on PATH,
        which launchd keeps minimal; and the credentials, the only login it is given."""
        env = {k: v for k, v in os.environ.items() if k not in self._scrub}
        env["PATH"] = os.path.dirname(cli) + os.pathsep + env.get("PATH", "")
        return env | self._credentials.env()

    def available(self) -> bool:
        return find_cli(self._binary, self._nvm_dir) is not None

    def check(self) -> str:
        """Starts the CLI as a request would (same environment, so a missing login shows), without a model call."""
        cli = self._cli()
        out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30,
                             env=self._env(cli), cwd=self._workdir if self._workdir.is_dir() else None)
        if out.returncode != 0:
            raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[:1] or "no output")
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else "ok"

    capabilities = Capabilities(images=True, pdf=True)  # photos and PDFs go to the model as they are

    def complete(self, system: str, prompt: str, *, json_mode: bool = False, attachments: Sequence[Attachment] = ()) -> str:
        self._workdir.mkdir(parents=True, exist_ok=True)
        cli = self._cli()
        env = self._env(cli)  # before the call: no login to give is a lapsed login, not a failure to stand in for
        # with files the message goes as stream-json (the files as content blocks), and the reply comes back the same way
        io = ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"] if attachments else ["--output-format", "text"]
        out = subprocess.run(
            [
                cli, "-p",
                "--model", self._model,
                "--no-session-persistence",
                "--tools", "",
                *SEALED,
                "--system-prompt", system,
                *io,
            ],
            input=user_message(prompt, attachments) if attachments else prompt, capture_output=True, text=True,
            timeout=self._timeout, cwd=self._workdir, env=env,
        )
        if out.returncode != 0:
            # the CLI reports auth and quota problems on stdout or stderr; keep the first line
            said = (out.stderr or out.stdout).strip()
            reason = said.splitlines()[:1]
            if LOGIN_LOST.search(said):
                raise CloudLoginLost(reason[0] if reason else "login lapsed")
            raise RuntimeError(f"claude CLI exited {out.returncode}: {reason[0] if reason else 'no output'}")
        said = stream_result(out.stdout) if attachments else out.stdout.strip()
        # some CLI versions print the login problem as the reply and exit 0: one short line, nothing else
        if "\n" not in said and len(said) < 200 and LOGIN_REPLY.search(said):
            raise CloudLoginLost(said)
        return said


def user_message(prompt: str, attachments: Sequence[Attachment]) -> str:
    """One stream-json user message: the files as content blocks, then the prompt."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": content_blocks(prompt, attachments)}},
                      ensure_ascii=False) + "\n"


def stream_result(stdout: str) -> str:
    """The reply in a stream-json run: its result line."""
    for line in reversed(stdout.splitlines()):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            return str(d.get("result") or "").strip()
    return ""


class ClaudeCliProvider:
    """The Claude CLI as a cloud provider (llm.providers.<name>.impl: claude): its models' backends (one per
    model and timeout), logged in as configured (binary, token_file)."""

    default_models = {"fast": "haiku", "standard": "sonnet", "strong": "opus"}
    location = "cloud"
    counts_tokens = False  # the CLI says nothing of tokens

    def __init__(self, workdir: Path, binary: str = "claude", token_file: str | None = None, nvm_dir: Path | None = None,
                 scrub: Iterable[str] = (), *, words: Language):
        """`scrub`: environment variables the CLI is not handed (another provider's API key: with one, the CLI
        would bill that key instead of logging in as configured)."""
        self.words = words
        self._workdir = workdir
        self._binary = binary
        self._credentials: Credentials = HeadlessToken(Path(os.path.expanduser(token_file))) if token_file else OwnLogin()
        self._nvm_dir = nvm_dir
        self._scrub = frozenset(scrub)
        self._made: dict[tuple[str, float], ClaudeCliBackend] = {}

    @staticmethod
    def label(words: Language) -> str:
        return "Claude"  # what the user calls this cloud

    @classmethod
    def from_spec(cls, spec: ProviderSpec, ctx: ProviderContext) -> "ClaudeCliProvider":
        o = spec.options
        return cls(ctx.data_dir / "llm-workdir", str(o.get("binary") or "claude"), o.get("token_file"),
                   Path(os.environ.get("NVM_DIR") or "~/.nvm").expanduser(), ctx.scrub, words=ctx.words)

    def models(self) -> list[str]:
        return list(dict.fromkeys(self.default_models.values()))  # the CLI's own names for them

    def backend(self, model: str, timeout: float = 120.0) -> ClaudeCliBackend:
        if (model, timeout) not in self._made:
            self._made[model, timeout] = ClaudeCliBackend(self._workdir, model=model, binary=self._binary, timeout=timeout,
                                                          credentials=self._credentials, nvm_dir=self._nvm_dir,
                                                          scrub=self._scrub, words=self.words)
        return self._made[model, timeout]
