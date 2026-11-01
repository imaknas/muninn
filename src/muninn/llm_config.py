"""Which cloud providers answer Muninn's model requests, read from config `llm`.

One entry per service, with where its key is and its model for each tier (agent-hub reads the same shape):

    llm:
      providers:
        anthropic: {impl: anthropic, key: {secret: anthropic-api-key},
                    models: {fast: claude-haiku-5-5, standard: claude-sonnet-5-5, strong: claude-opus-5-5}}
        claude:    {impl: claude, token_file: ~/.claude/headless-oauth-token}
      use: {fast: anthropic, standard: anthropic}

Each provider names its implementation (`impl`), where its key is — `{secret: <name>}`, a file of that name in
the secrets directory, or `{env: <NAME>}`, never the key itself — and its own model for each tier it is asked
for (a tier it leaves out takes the implementation's default). `use` says which provider serves each tier; with
one provider it may be left out. The user's own words (routing, facts, answers) go to the `standard` tier,
everything else to `fast`; `llm.models` can move one kind of work to another tier, another model of the same
provider, or `{provider, model}`; `llm.points` does the same for one decision point (contracts/llm-points.yaml).

A provider with `location: local` (Ollama's default) runs on this machine or this network: its base_url must be
loopback, a private address, the tailnet or Docker's host, and it is the local model privacy routing keeps
sensitive content on. There is one at most; `use`, `llm.models` and `llm.points` name cloud providers only.

A provider may give what its models cost (`prices: {<model>: {input: 3, output: 15}}`, US dollars per million
tokens), which is what a daily limit (budget.daily_usd, core/budget.py) counts.

A config without `providers` is read the older way (llm.cloud, claude_binary, claude_token_file, cloud_model,
understanding_model; llm.local_model and ollama_url, as the provider "ollama") into the same shape, so it keeps
working unchanged.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Mapping, Protocol
from urllib.parse import urlsplit

from muninn.core.budget import Price
from muninn.core.language import Language
from muninn.core.ports import LLMBackend
from muninn.core.task_models import GROUPS, ModelChoice, TaskModels

TIERS = ("fast", "standard", "strong")
GROUP_TIER = {g.key: g.tier for g in GROUPS}
POINT_GROUP = {t: g.key for g in GROUPS for t in g.tasks}  # a decision point's group; one in none takes the fast tier
LEGACY_CLOUD = {"claude-cli": "claude"}  # llm.cloud's names → the implementation, under the provider name "claude"


class Provider(Protocol):
    def backend(self, model: str, timeout: float = 120.0) -> LLMBackend:
        """That model's backend (one per model and timeout)."""
        ...

    def models(self) -> list[str]:
        """The models the service offers, as it lists them (its key checked on the way; nothing is asked of a
        model). Empty when the service keeps no list: its models are then named by hand."""
        ...


class Implementation(Protocol):
    """One kind of provider (llm.providers.<name>.impl): the class, made into a provider from its config."""
    default_models: Mapping[str, str]  # tier → model, for a tier its config leaves out
    location: str  # where it runs unless config says: "cloud", or "local" (this machine or network)
    counts_tokens: bool  # its replies say how many tokens they took (what a spending limit counts)

    def label(self, words: Language) -> str:
        """What the user calls it."""
        ...

    def from_spec(self, spec: ProviderSpec, ctx: ProviderContext) -> Provider:
        """The provider as config describes it; ValueError for what it cannot use as written."""
        ...


@dataclass(frozen=True)
class ProviderContext:
    """What every implementation is made with, beside its own spec."""
    data_dir: Path
    secrets_dir: Path
    scrub: frozenset[str] = frozenset()  # environment variables holding a provider's key: no program is handed them
    words: Language = field(kw_only=True)  # the language what a provider tells the user (a label, how to log in) is in


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    impl: str
    models: dict[str, str]  # tier → model: the implementation's defaults under what config names
    key: dict[str, str] | None = None  # {"secret": name} or {"env": NAME}
    options: dict[str, Any] = field(default_factory=dict)  # the rest, for the implementation (binary, base_url …)
    location: str = "cloud"  # "local": on this machine or network, for content that may not leave it
    contexts: dict[str, int] = field(default_factory=dict)  # model → how much it reads at once (tokens), where given
    prices: dict[str, Price] = field(default_factory=dict)  # model → what it costs, where given


@dataclass(frozen=True)
class LLMSetup:
    providers: dict[str, ProviderSpec]
    use: dict[str, str]  # tier → provider name
    overrides: dict[str, Any]  # config llm.models: group → tier, model, or {provider, model}
    point_overrides: dict[str, Any] = field(default_factory=dict)  # config llm.points: decision point → the same

    @property
    def local(self) -> ProviderSpec | None:
        """The local provider, if there is one."""
        return next((p for p in self.providers.values() if p.location == "local"), None)

    @property
    def cloud(self) -> bool:
        return any(p.location == "cloud" for p in self.providers.values())

    def choice(self, tier: str) -> ModelChoice:
        """The provider and model serving a tier; ValueError when `use` names no provider for it."""
        if tier not in self.use:
            raise ValueError(f"llm.use: no provider for the {tier} tier; providers: {', '.join(self.providers)}")
        name = self.use[tier]
        if tier not in self.providers[name].models:
            raise ValueError(f"llm.providers.{name}.models: no model for the {tier} tier (llm.use gives it that tier)")
        return ModelChoice(name, self.providers[name].models[tier])

    def task_models(self) -> TaskModels:
        defaults = {g.key: self.choice(GROUP_TIER[g.key]) for g in GROUPS}
        points = {p: self._resolve(POINT_GROUP.get(p, ""), v, f"llm.points.{p}") for p, v in self.point_overrides.items() if v}
        return TaskModels(defaults, {k: self._resolve(k, v) for k, v in self.overrides.items() if v}, points)

    def _resolve(self, group: str, v: Any, where: str = "") -> ModelChoice:
        if isinstance(v, dict):
            name = v.get("provider")
            if name in self.providers and self.providers[name].location == "local":
                raise ValueError(f"{where or 'llm.models.' + group}: {name} is the local provider; it serves only what may not "
                                 "leave this machine (privacy routing), not a kind of work or a decision point")
            if name not in self.providers:
                raise ValueError(f"{where or 'llm.models.' + group}: provider {name!r}; known: {', '.join(self.providers)}")
            return ModelChoice(name, str(v.get("model") or self.providers[name].models[GROUP_TIER.get(group, "fast")]))
        if v in TIERS:
            return self.choice(v)
        return ModelChoice(self.choice(GROUP_TIER.get(group, "fast")).provider, str(v))

    def prices(self) -> dict[str, Price]:
        """What each priced model costs, by its backend's name (provider:model)."""
        return {f"{p.name}:{m}": price for p in self.providers.values() for m, price in p.prices.items()}

    def env_keys(self) -> set[str]:
        """The environment variables that hold some provider's key: no other program is handed them."""
        return {p.key["env"] for p in self.providers.values() if p.key and "env" in p.key}


def llm_setup(llm: Mapping[str, Any], impls: Mapping[str, Implementation],
              known_points: Collection[str] | None = None) -> LLMSetup | None:
    """The providers config names (None: no cloud model at all). ValueError for anything it cannot use as written;
    `known_points`, the decision points there are, for llm.points."""
    if "providers" in llm and llm["providers"] is not None:
        raw = dict(llm["providers"])
        use = dict(llm.get("use") or {})
    else:
        legacy = llm.get("cloud")
        if legacy and legacy != "none" and legacy not in LEGACY_CLOUD:
            raise ValueError(f"llm.cloud: {legacy!r}; known: {', '.join(sorted(LEGACY_CLOUD))}, none")
        models = {t: m for t, m in (("fast", llm.get("cloud_model")), ("standard", llm.get("understanding_model"))) if m}
        raw = {"claude": {"impl": LEGACY_CLOUD[legacy], "binary": llm.get("claude_binary"),
                          "token_file": llm.get("claude_token_file"), "models": models}} if legacy and legacy != "none" else {}
        use = {}
    if llm.get("local_model"):  # the older way to name the local model: the provider "ollama", as before
        if any(isinstance(s, dict) and (s.get("location") or impls.get(s.get("impl"), _Cloud).location) == "local" for s in raw.values()):
            raise ValueError("llm.local_model and a local provider in llm.providers both name the local model: keep one")
        raw.setdefault("ollama", {"impl": "ollama", "base_url": llm.get("ollama_url"), "models": {"fast": llm["local_model"]}})
    if not raw:
        return None
    providers = {name: _provider(str(name), spec, impls) for name, spec in raw.items()}
    if len([p for p in providers.values() if p.location == "local"]) > 1:
        raise ValueError("llm.providers: one local provider at most; the others can be cloud (location: cloud)")
    if (local := next((p for p in providers.values() if p.location == "local"), None)) and "fast" not in local.models:
        raise ValueError(f"llm.providers.{local.name}.models: the local model goes under fast, e.g. {{fast: qwen3:8b}}")
    cloud = [n for n, p in providers.items() if p.location == "cloud"]
    if not use and len(cloud) == 1:
        use = {t: cloud[0] for t in TIERS}
    for tier, name in use.items():
        if tier not in TIERS:
            raise ValueError(f"llm.use: unknown tier {tier!r}; known: {', '.join(TIERS)}")
        if name not in providers:
            raise ValueError(f"llm.use.{tier}: provider {name!r}; known: {', '.join(providers)}")
        if providers[name].location == "local":
            raise ValueError(f"llm.use.{tier}: {name} is the local provider; use names cloud providers")
    points = dict(llm.get("points") or {})
    if known_points is not None and (unknown := set(points) - set(known_points)):
        raise ValueError(f"llm.points: unknown {sorted(unknown)}; the decision points are in contracts/llm-points.yaml")
    return LLMSetup(providers, use, dict(llm.get("models") or {}), points)


def _provider(name: str, spec: Any, impls: Mapping[str, Implementation]) -> ProviderSpec:
    where = f"llm.providers.{name}"
    if not isinstance(spec, dict):
        raise ValueError(f"{where}: expected a mapping with impl, models and (for an API) key")
    spec = dict(spec)
    impl = spec.pop("impl", None)
    if impl not in impls:
        raise ValueError(f"{where}.impl: {impl!r}; known: {', '.join(sorted(impls))}")
    location = spec.pop("location", None) or impls[impl].location
    if location not in ("cloud", "local"):
        raise ValueError(f"{where}.location: {location!r}; cloud or local")
    if location == "local" and not private(str(spec.get("base_url") or "http://127.0.0.1")):
        raise ValueError(f"{where}.base_url: a local provider runs on this machine or this network (loopback, a private "
                         f"address, the tailnet or host.docker.internal); {spec.get('base_url')} is not one")
    named = spec.pop("models", None) or {}
    if unknown := set(named) - set(TIERS):
        raise ValueError(f"{where}.models: unknown tier(s) {sorted(unknown)}; known: {', '.join(TIERS)}")
    models = {**impls[impl].default_models, **{t: _model_name(m) for t, m in named.items() if m}}
    contexts = {_model_name(m): int(m["context"]) for m in named.values() if isinstance(m, dict) and m.get("context")}
    prices = _prices(f"{where}.prices", spec.pop("prices", None) or {})
    return ProviderSpec(name, impl, models, _key(where, spec.pop("key", None)), {k: v for k, v in spec.items() if v is not None},
                        location, contexts, prices)


def _prices(where: str, given: Any) -> dict[str, Price]:
    if not isinstance(given, dict):
        raise ValueError(f"{where}: a model → {{input, output}} mapping (US dollars per million tokens)")
    out = {}
    for model, p in given.items():
        try:
            out[str(model)] = Price(float(p["input"]), float(p["output"]))
        except (TypeError, KeyError, ValueError):
            raise ValueError(f"{where}.{model}: {{input: <dollars>, output: <dollars>}} per million tokens") from None
    return out


class _Cloud:
    location = "cloud"


def private(url: str) -> bool:
    """A URL on this machine or this network: loopback, a private or tailnet (100.64/10) address, a *.ts.net
    name, localhost, or Docker's host."""
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith((".ts.net", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")


def _model_name(m: Any) -> str:
    """A model as config writes it: its name, or {name, context} (context: how much it reads at once)."""
    return str(m["name"]) if isinstance(m, dict) else str(m)


def _key(where: str, key: Any) -> dict[str, str] | None:
    if key is None:
        return None
    if isinstance(key, dict) and len(key) == 1:
        (kind, value), = key.items()
        if kind == "secret" and isinstance(value, str) and re.fullmatch(r"[\w.-]+", value):
            return {"secret": value}
        if kind == "env" and isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return {"env": value}
    # never echo what was written: it may be the key itself
    raise ValueError(f"{where}.key: write where the key is, not the key — {{secret: <file name in the secrets "
                     f"directory>}} or {{env: <VARIABLE>}}")
