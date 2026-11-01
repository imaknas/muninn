"""Cloud backend: the `codex` CLI run non-interactively (`codex exec`), sealed as the claude backend is.

Codex is an agent: left as it is, a model reading a mail Muninn hands it could run commands, read files, start
helper agents or search the web. Unlike the claude CLI it has no one switch for all of that, so each part is
switched off on its own (`SEALED`): its tools and sub-agents (TOOLS_OFF), web search, the user's own Codex settings,
MCP servers and rules (not loaded), and its coding-agent instructions (replaced by Muninn's system prompt). What is
left offered fails closed: tried against Codex 0.165, a model told to read a file with Code Mode's exec or a helper
agent could do neither, and said so. Nothing is written to ~/.codex/sessions (`--ephemeral`), so these calls never
show as the user's own Codex activity either.

It logs in as the CLI is logged in (`codex login`: a ChatGPT account or an API key). Unlike the claude CLI, every
reply says how many tokens it took, so a spending limit can count it. With no model configured for a tier, Codex
uses its own default; the models it lists are the ones Codex itself shows.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from muninn.adapters.llm.claude_cli import find_cli
from muninn.core.language import Language
from muninn.core.models import Attachment
from muninn.core.ports import Capabilities, CloudLoginLost, Reply
from muninn.llm_config import ProviderContext, ProviderSpec

DEFAULT = "default"  # a tier without a model of its own: Codex's own default model
# every feature that gives the model a tool, an agent or a way out of the reply (Codex 0.165: `codex features list`)
TOOLS_OFF = ("shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "view_image", "apps", "browser_use",
             "browser_use_external", "in_app_browser", "computer_use", "image_generation", "multi_agent", "plugins",
             "hooks", "skill_search", "sleep_tool", "tool_suggest", "goals", "code_mode_host", "in_app_local_automation")
SEALED = ["--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-s", "read-only",
          *[x for f in TOOLS_OFF for x in ("--disable", f)],
          "-c", 'web_search="disabled"', "-c", "include_apps_instructions=false",
          "-c", "include_collaboration_mode_instructions=false", "-c", "include_permissions_instructions=false"]
LOGIN_LOST = re.compile(r"(?i)not logged in|codex login|please log in|\b401\b|unauthori[sz]ed|token (has )?expired|invalid api key")
IMAGE_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


def reply_of(stdout: str) -> tuple[str, int | None, int | None, str]:
    """From `codex exec --json`'s events: the last message, the tokens in and out, and what failed (else "")."""
    said, tokens_in, tokens_out, failed = "", None, None, ""
    for raw in stdout.splitlines():
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        item = e.get("item") or {}
        if e.get("type") == "item.completed" and item.get("type") == "agent_message":
            said = str(item.get("text") or "")
        elif e.get("type") == "turn.completed":
            usage = e.get("usage") or {}
            tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
        elif e.get("type") in ("turn.failed", "error"):
            failed = str((e.get("error") or {}).get("message") or e.get("message") or "failed")
    return said.strip(), tokens_in, tokens_out, failed


class CodexCliBackend:
    is_local = False
    label = "Codex"
    capabilities = Capabilities(images=True)  # photos go along as images; a PDF it cannot read

    def __init__(self, workdir: Path, model: str = DEFAULT, binary: str = "codex", timeout: float = 120.0,
                 nvm_dir: Path | None = None, scrub: Iterable[str] = (), *, words: Language,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        """`binary`: as configured (see find_cli); `scrub`: environment variables it is not handed (another
        provider's API key, which Codex would otherwise bill)."""
        self.login_hint = words("llm.codex_login")
        self.name = f"codex-cli:{model}"
        self._workdir, self._model, self._binary, self._timeout = Path(workdir), model, binary, timeout
        self._nvm_dir, self._scrub, self._run = nvm_dir, frozenset(scrub), run

    def _cli(self) -> str:
        found = find_cli(self._binary, self._nvm_dir, "codex")
        if found is None:
            raise FileNotFoundError(f"codex CLI not found ({self._binary})")
        return found

    def _env(self, cli: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in self._scrub}
        env["PATH"] = os.path.dirname(cli) + os.pathsep + env.get("PATH", "")  # launchd keeps PATH minimal
        return env

    def available(self) -> bool:
        return find_cli(self._binary, self._nvm_dir, "codex") is not None

    def check(self) -> str:
        """Whether the CLI is there and logged in, without a model call."""
        cli = self._cli()
        out = self._run([cli, "login", "status"], capture_output=True, text=True, timeout=30, env=self._env(cli))
        said = (out.stdout or out.stderr).strip()
        if out.returncode != 0:
            raise CloudLoginLost(said.splitlines()[0] if said else "not logged in")
        return said.splitlines()[0] if said else "ok"

    def complete(self, system: str, prompt: str, *, json_mode: bool = False, attachments: Sequence[Attachment] = ()) -> Reply:
        self._workdir.mkdir(parents=True, exist_ok=True)
        cli = self._cli()
        with tempfile.TemporaryDirectory(dir=self._workdir) as tmp:
            # Codex reads its instructions from a file: Muninn's system prompt in place of its own
            instructions = Path(tmp) / "instructions.md"
            instructions.write_text(system, encoding="utf-8")
            images = []
            for i, a in enumerate(x for x in attachments if x.is_image):
                path = Path(tmp) / f"image-{i}{IMAGE_SUFFIX.get(a.media_type, '')}"
                path.write_bytes(a.data)
                images += ["-i", str(path)]
            out = self._run(
                [cli, "exec", *SEALED, "-C", tmp, "--json",
                 "-c", f"model_instructions_file={json.dumps(str(instructions))}",
                 *(["-m", self._model] if self._model != DEFAULT else []), *images, "-"],
                input=prompt, capture_output=True, text=True, timeout=self._timeout, cwd=tmp, env=self._env(cli))
        said, tokens_in, tokens_out, failed = reply_of(out.stdout or "")
        if out.returncode != 0 or failed or not said:
            lines = (failed or out.stderr or out.stdout or "").strip().splitlines()
            why = lines[-1] if lines else "no reply"
            if LOGIN_LOST.search(failed or out.stderr or ""):
                raise CloudLoginLost(why)
            raise RuntimeError(f"codex exec exited {out.returncode}: {why}")
        return Reply(said, tokens_in, tokens_out)


class CodexCliProvider:
    """The Codex CLI as a cloud provider (llm.providers.<name>.impl: codex): its models' backends (one per model
    and timeout), logged in as the CLI is."""

    default_models = {"fast": DEFAULT, "standard": DEFAULT, "strong": DEFAULT}
    location = "cloud"
    counts_tokens = True  # every reply says what it took

    def __init__(self, workdir: Path, binary: str = "codex", nvm_dir: Path | None = None, scrub: Iterable[str] = (),
                 models_file: Path | None = None, *, words: Language):
        """`models_file`: where Codex keeps the models it shows (its models_cache.json)."""
        self.words = words
        self._workdir, self._binary, self._nvm_dir, self._scrub, self._models_file = workdir, binary, nvm_dir, frozenset(scrub), models_file
        self._made: dict[tuple[str, float], CodexCliBackend] = {}

    @staticmethod
    def label(words: Language) -> str:
        return "Codex"  # what the user calls this cloud

    @classmethod
    def from_spec(cls, spec: ProviderSpec, ctx: ProviderContext) -> "CodexCliProvider":
        home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        return cls(ctx.data_dir / "llm-workdir", str(spec.options.get("binary") or "codex"),
                   Path(os.environ.get("NVM_DIR") or "~/.nvm").expanduser(), ctx.scrub, home / "models_cache.json", words=ctx.words)

    def models(self) -> list[str]:
        """The models Codex itself shows, in its order (none known: its default alone)."""
        try:
            listed: list[dict[str, Any]] = json.loads(self._models_file.read_text()).get("models") or [] if self._models_file else []
        except (OSError, ValueError, AttributeError):
            listed = []
        shown = sorted((m for m in listed if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug")),
                       key=lambda m: m.get("priority", 0))
        return [str(m["slug"]) for m in shown] or [DEFAULT]

    def backend(self, model: str, timeout: float = 120.0) -> CodexCliBackend:
        if (model, timeout) not in self._made:
            self._made[model, timeout] = CodexCliBackend(self._workdir, model=model, binary=self._binary, timeout=timeout,
                                                         nvm_dir=self._nvm_dir, scrub=self._scrub, words=self.words)
        return self._made[model, timeout]
