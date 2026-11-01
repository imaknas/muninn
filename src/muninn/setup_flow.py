"""First-run setup from the browser: which provider answers, its key, its models, and the phone's pairing.

The key goes to a file of its own in the secrets directory (only its owner reads it) before anything else; the
config gets only where it is ({secret: <file>}); no response, log line or URL carries it. Config is rewritten in
place, keeping what else it says and its comments, through a temporary file. Settings take effect when Muninn
starts again: under a service manager (MUNINN_SERVICE set by the install) it restarts itself; otherwise the page
says to.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

import segno
from ruamel.yaml import YAML

from muninn.config import Config
from muninn.languages import CODES, language
from muninn.llm_config import Implementation, ProviderContext, llm_setup

# what the setup page offers: impl → how it is asked for (the page names each in its language)
CHOICES = {
    "anthropic": {"needs_key": True, "base_url": None},
    "openai": {"needs_key": True, "base_url": "https://api.openai.com/v1"},
    "ollama": {"needs_key": False, "base_url": "http://127.0.0.1:11434"},
}


class Setup:
    def __init__(self, cfg: Config, impls: Mapping[str, Implementation], restart: Callable[[], bool], token: str | None = None):
        """`restart()`: starts Muninn again with the new settings, saying whether it will (False: the user must);
        `token`: the one-time setup token printed at start, while no device is paired."""
        self._cfg, self._impls, self._restart, self.token = cfg, impls, restart, token

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
        folder = self._cfg.secrets_dir
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / f"{name}-api-key"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.strip() + "\n")
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

    def set_language(self, code: str) -> bool:
        """Writes the language Muninn speaks into config, then starts Muninn again; False when the user has to."""
        if code not in CODES:
            raise ValueError(f"language: one of {', '.join(CODES)}")
        self._rewrite(lambda doc: doc.__setitem__("language", code))
        return self._restart()

    def _rewrite(self, change: Callable[[Any], None]) -> None:
        """config.yaml changed by `change` in place (what else it says and its comments kept), through a temporary
        file; nothing is written when `change` refuses."""
        path = self._cfg.data_dir / "config.yaml"
        y = YAML()
        y.preserve_quotes = True
        doc = y.load(path.read_text(encoding="utf-8")) if path.exists() else None
        doc = doc if doc is not None else y.load("{}")
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
