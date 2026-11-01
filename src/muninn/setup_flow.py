"""Setting Muninn up from the browser: first which provider answers, its key, its models, and the phone's pairing;
then, from the settings page, the folders it reads activity from and the Google client it connects with.

The key goes to a file of its own in the secrets directory (only its owner reads it) before anything else; the
config gets only where it is ({secret: <file>}); no response, log line or URL carries it. Config is rewritten in
place, keeping what else it says and its comments, through a temporary file. Settings take effect when Muninn
starts again: under a service manager (MUNINN_SERVICE set by the install) it restarts itself; otherwise the page
says to.
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

import segno
from ruamel.yaml import YAML

from muninn.config import Config
from muninn.languages import CODES, language
from muninn.languages import language as words_for  # where a parameter is called language
from muninn.llm_config import Implementation, ProviderContext, llm_setup

# what the setup page offers: impl → how it is asked for (the page names each in its language)
# what a model chosen in setup is asked once, to see it answers at all (not a decision point: nothing is decided on it)
PROBE = ("Answer with the single word OK.", "Are you there?")
# where people keep their code, and where a coding agent keeps its sessions: offered on the settings page when found
WORK_FOLDERS = ("workspace", "code", "projects", "src", "dev", "Developer", "repos", "GitHub")
SESSION_FOLDERS = (".claude/projects",)
SOURCES = ("workspace_roots", "transcript_roots")
CHOICES = {
    "anthropic": {"needs_key": True, "base_url": None},
    "openai": {"needs_key": True, "base_url": "https://api.openai.com/v1"},
    "ollama": {"needs_key": False, "base_url": "http://127.0.0.1:11434"},
}


class Setup:
    def __init__(self, cfg: Config, impls: Mapping[str, Implementation], restart: Callable[[], bool], token: str | None = None,
                 home: Path | None = None):
        """`restart()`: starts Muninn again with the new settings, saying whether it will (False: the user must);
        `token`: the one-time setup token printed at start, while no device is paired; `home`: the user's home
        folder, where folders to read are looked for (None: none are suggested)."""
        self._cfg, self._impls, self._restart, self.token, self._home = cfg, impls, restart, token, home

    def state(self) -> dict[str, Any]:
        llm = self._cfg["llm"]
        return {"choices": [{"impl": k, **v} for k, v in CHOICES.items()], "public_url": self._cfg.public_url,
                "language": self._cfg.language,
                "has_model": bool(llm.get("providers") or llm.get("local_model") or llm.get("cloud") not in (None, "none"))}

    def _spec(self, choice: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        impl = str(choice.get("impl") or "")
        if impl not in CHOICES:
            raise ValueError(f"impl: one of {', '.join(CHOICES)}")
        label = str(choice.get("label") or "").strip()
        name = impl if impl != "openai" or not label else re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "openai"
        spec: dict[str, Any] = {"impl": impl}
        if base := str(choice.get("base_url") or "").strip():
            spec["base_url"] = base
        if label and impl == "openai":
            spec["label"] = label
        if CHOICES[impl]["needs_key"] and not choice.get("local"):
            spec["key"] = {"secret": f"{name}-api-key"}
        if choice.get("local") and impl == "openai":
            spec["location"] = "local"
        return name, spec

    def _write_key(self, name: str, key: str) -> None:
        self._write_secret(f"{name}-api-key", key.strip() + "\n")

    def _write_secret(self, name: str, text: str) -> None:
        """A file in the secrets folder that only its owner reads."""
        folder = self._cfg.secrets_dir
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        path.chmod(0o600)

    def models(self, choice: Mapping[str, Any]) -> list[str]:
        """The models the chosen service lists, its key (if given) kept first: the key is checked by the listing."""
        name, spec = self._spec(choice)
        if key := str(choice.get("key") or "").strip():
            if "key" not in spec:
                raise ValueError("this service takes no key")
            self._write_key(name, key)
        setup = llm_setup({"providers": {name: {**spec, "models": {"fast": "x"}}}}, self._impls)
        assert setup is not None
        ctx = ProviderContext(self._cfg.data_dir, self._cfg.secrets_dir, words=language(self._cfg.language))
        return self._impls[spec["impl"]].from_spec(setup.providers[name], ctx).models()

    def save(self, choice: Mapping[str, Any], fast: str, standard: str | None = None, language: str | None = None) -> bool:
        """Writes the provider and its models into config (cloud routing for a cloud provider; a local one is the
        local model), and the language the setup was done in, then starts Muninn again; False when the user has to."""
        name, spec = self._spec(choice)
        if not fast:
            raise ValueError("fast: the model for most of the work")
        if language is not None and language not in CODES:
            raise ValueError(f"language: one of {', '.join(CODES)}")
        local = spec["impl"] == "ollama" or spec.get("location") == "local"
        spec["models"] = {"fast": fast} if local else {"fast": fast, "standard": standard or fast}
        words = words_for(language or self._cfg.language)
        for model in dict.fromkeys(spec["models"].values()):  # each model chosen answers before anything is written
            self._answers(name, spec, model, words)

        def change(doc: Any) -> None:
            llm = doc.setdefault("llm", {})
            llm.setdefault("providers", {})[name] = spec
            if local:
                llm.pop("local_model", None)  # the provider is the local model now
                llm["routing"] = llm.get("routing") or "privacy"
            else:
                llm["use"] = {"fast": name, "standard": name}
                llm["routing"] = "cloud"
            llm_setup(dict(llm), self._impls)  # what cannot work is said here, before anything is written
            if language:
                doc["language"] = language

        self._rewrite(change)
        return self._restart()

    def _answers(self, name: str, spec: Mapping[str, Any], model: str, words: Any) -> None:
        """Asks the model one short question, as Muninn will ask it things: one that cannot hold a conversation (an
        embedding model, one the key may not use) is refused now, not found out after setup. ValueError, said."""
        setup = llm_setup({"providers": {name: {**spec, "models": {"fast": model}}}}, self._impls)
        assert setup is not None
        ctx = ProviderContext(self._cfg.data_dir, self._cfg.secrets_dir, words=words)
        backend = self._impls[spec["impl"]].from_spec(setup.providers[name], ctx).backend(model, 120)
        try:
            reply = backend.complete(*PROBE)
        except Exception as e:  # noqa: BLE001 — whatever the service says is what the user is told
            raise ValueError(words("setup_flow.no_answer", model=model, why=str(e)[:200])) from None
        if not str(reply or "").strip():
            raise ValueError(words("setup_flow.no_answer", model=model, why="(empty)"))

    def set_public_url(self, url: str) -> bool:
        """Writes the HTTPS address the phone reaches Muninn at, then starts Muninn again; False when the user has to."""
        if not url.startswith("https://"):
            raise ValueError("public_url: an https:// address")
        self._rewrite(lambda doc: doc.__setitem__("public_url", url))
        return self._restart()

    def set_language(self, code: str) -> bool:
        """Writes the language Muninn speaks into config, then starts Muninn again; False when the user has to."""
        if code not in CODES:
            raise ValueError(f"language: one of {', '.join(CODES)}")
        self._rewrite(lambda doc: doc.__setitem__("language", code))
        return self._restart()

    # ── the settings page: folders to read, the Google client ──────────────────

    def sources(self) -> dict[str, Any]:
        """The folders Muninn reads activity from, as config says now (saved, though maybe not running yet), and the
        usual ones found in the home folder that are not listed."""
        doc = self._doc()
        now = {k: [str(x) for x in (doc.get(k) or [])] for k in SOURCES}
        found = {"workspace_roots": WORK_FOLDERS, "transcript_roots": SESSION_FOLDERS}

        def suggested(key: str) -> list[str]:
            if self._home is None:
                return []
            listed = {str(self._expand(x)) for x in now[key]}
            return [self._shown(d) for d in (self._home / n for n in found[key]) if d.is_dir() and str(d) not in listed]
        return {**now, "suggested": {k: suggested(k) for k in SOURCES}}

    def _expand(self, path: str) -> Path:
        """A folder as written (~/… under the home folder) as it is on this computer."""
        if self._home is not None and (path == "~" or path.startswith("~/")):
            return self._home / path[2:]
        return Path(path).expanduser()

    def _shown(self, folder: Path) -> str:
        """A folder as config writes it: under the home folder as ~/…, so the config moves with the user."""
        if self._home is not None and folder.is_relative_to(self._home):
            return "~/" + str(folder.relative_to(self._home))
        return str(folder)

    def set_sources(self, folders: Mapping[str, Any]) -> bool:
        """The folders to read activity from (each an existing folder), written into config; then Muninn starts again
        to read them, or False when the user has to start it."""
        words = language(self._cfg.language)
        clean: dict[str, list[str]] = {}
        for key in SOURCES:
            out: list[str] = []
            for raw in folders.get(key) or []:
                path = str(raw).strip()
                if not path:
                    continue
                if not self._expand(path).is_dir():
                    raise ValueError(words("setup_flow.no_folder", path=path))
                if path not in out:
                    out.append(path)
            clean[key] = out

        def change(doc: Any) -> None:
            for key, paths in clean.items():
                doc[key] = paths
        self._rewrite(change)
        return self._restart()

    def google_callback(self) -> str | None:
        """Where Google sends the user back after signing in: Muninn's HTTPS address (none yet: Google cannot)."""
        return f"{self._cfg.public_url.rstrip('/')}/oauth/google/callback" if self._cfg.public_url else None

    def google_client(self, text: str, gmail: bool) -> bool:
        """The OAuth client file the user downloaded from Google, checked for what signing in will need (a web
        application, with Muninn's address to come back to) and kept in the secrets folder; with `gmail`, a Gmail
        account to read is added unless one is listed. Then Muninn starts again, or False when the user has to."""
        words = language(self._cfg.language)
        callback = self.google_callback()
        if callback is None:
            raise ValueError(words("setup_flow.google_needs_https"))
        try:
            raw = json.loads(text)
        except ValueError:
            raise ValueError(words("setup_flow.google_not_json")) from None
        if isinstance(raw, dict) and "installed" in raw:
            raise ValueError(words("setup_flow.google_desktop"))
        web = raw.get("web") if isinstance(raw, dict) else None
        if not isinstance(web, dict) or not web.get("client_id") or not web.get("client_secret"):
            raise ValueError(words("setup_flow.google_not_client"))
        if callback not in (web.get("redirect_uris") or []):
            raise ValueError(words("setup_flow.google_redirect", uri=callback))
        self._write_secret("google_client.json", json.dumps({"web": web}, indent=1) + "\n")
        if gmail:
            def change(doc: Any) -> None:
                accounts = doc.get("mail_accounts") or []
                if not any((a or {}).get("provider", "gmail") == "gmail" for a in accounts):
                    doc["mail_accounts"] = [*accounts, {"name": "personal", "kind": "personal", "provider": "gmail"}]
            self._rewrite(change)
        return self._restart()

    def _doc(self) -> Any:
        path = self._cfg.data_dir / "config.yaml"
        y = YAML()
        y.preserve_quotes = True
        doc = y.load(path.read_text(encoding="utf-8")) if path.exists() else None
        return doc if doc is not None else y.load("{}")

    def _rewrite(self, change: Callable[[Any], None]) -> None:
        """config.yaml changed by `change` in place (what else it says and its comments kept), through a temporary
        file; nothing is written when `change` refuses."""
        path = self._cfg.data_dir / "config.yaml"
        y = YAML()
        y.preserve_quotes = True
        doc = self._doc()
        change(doc)
        out = io.StringIO()
        y.dump(doc, out)
        tmp = path.with_suffix(".yaml.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out.getvalue())
        os.replace(tmp, path)


def qr_svg(url: str) -> str:
    """The URL as a QR code the phone's camera opens, as an inline SVG."""
    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="svg", scale=6, border=2, xmldecl=False, svgns=True)
    return out.getvalue().decode()
