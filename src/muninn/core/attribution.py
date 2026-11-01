"""Map observations to a domain (and optionally a goal) and fix their sensitivity.

Rules are deterministic. Anything no rule resolves stays unattributed, which
makes it sensitive; the engine may ask the local-only classifier for a
suggestion, but a suggestion only becomes a rule after the user confirms it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .models import Observation, Sensitivity


@dataclass(frozen=True)
class Attribution:
    domain: str | None = None
    goal: str | None = None  # qualified "<domain>/<goal>"


class Rule(Protocol):
    def match(self, obs: Observation) -> Attribution | None: ...


def _split(value: str | Mapping[str, Any]) -> Attribution:
    if isinstance(value, str):
        return Attribution(domain=value)
    goal = value.get("goal")
    domain = value.get("domain")
    if goal and "/" not in goal and domain:
        goal = f"{domain}/{goal}"
    return Attribution(domain=domain, goal=goal)


@dataclass(frozen=True)
class RepoRule:
    """payload.repo (a repository's directory name) → domain[, goal]."""

    repo: str
    result: Attribution

    def match(self, obs: Observation) -> Attribution | None:
        return self.result if obs.payload.get("repo") == self.repo else None


@dataclass(frozen=True)
class AppRule:
    """A window's app (whatever its title) → domain; the user said time in it counts there.
    Matched without regard to case: people name apps as they say them."""

    app: str
    result: Attribution

    def match(self, obs: Observation) -> Attribution | None:
        app = str(obs.payload.get("app") or "")
        return self.result if app and app.lower() == self.app.lower() else None


@dataclass(frozen=True)
class LanguageGoalRule:
    """A language in payload.langs → the goal it counts toward (domain stays open)."""

    language: str
    goal: str

    def match(self, obs: Observation) -> Attribution | None:
        if self.language in (obs.payload.get("langs") or ()):
            return Attribution(goal=self.goal)
        return None


@dataclass(frozen=True)
class WindowRule:
    """App name and/or window-title regex (for window-activity sources)."""

    app: str | None
    title: re.Pattern[str] | None
    result: Attribution

    def match(self, obs: Observation) -> Attribution | None:
        if self.app and obs.payload.get("app") != self.app:
            return None
        if self.title and not self.title.search(str(obs.payload.get("title") or "")):
            return None
        if not (self.app or self.title):
            return None
        return self.result


@dataclass(frozen=True)
class FieldRule:
    """A payload field equal to a value (e.g. browser_profile = work)."""

    field: str
    value: str
    result: Attribution

    def match(self, obs: Observation) -> Attribution | None:
        return self.result if str(obs.payload.get(self.field, "")) == self.value else None


@dataclass(frozen=True)
class ShownFileRule:
    """A window showing a file a deep talk gave the user (titled by the file's id, as a browser names it) belongs
    where that talk's project belongs."""

    files: Mapping[str, Attribution]  # file id → where its project belongs

    def match(self, obs: Observation) -> Attribution | None:
        title = str(obs.payload.get("title") or "")
        return next((self.files[w] for w in re.findall(r"\b[0-9a-f]{16}\b", title) if w in self.files), None)


@dataclass(frozen=True)
class TitleProjectRule:
    """A window title naming a known project (e.g. the folder in an editor
    or terminal title) belongs where that project belongs."""

    project: str
    result: Attribution

    def match(self, obs: Observation) -> Attribution | None:
        title = str(obs.payload.get("title") or "")
        if title and re.search(rf"(?<![\w-]){re.escape(self.project)}(?![\w-])", title, re.IGNORECASE):
            return self.result
        return None


@dataclass(frozen=True)
class KnownTitleRule:
    """Window titles already classified (by the local model, or the user)."""

    known: Mapping[str, Mapping[str, Any]]

    def match(self, obs: Observation) -> Attribution | None:
        if is_generic_window(obs.payload):
            return None  # its area depends on the moment, not the title
        hit = self.known.get(window_key(obs.payload))
        if not hit or not hit.get("domain") or hit.get("sure") is False:
            return None  # an unsure label counts toward nothing
        return Attribution(domain=hit["domain"], goal=hit.get("goal"))


def is_generic_window(payload: Mapping[str, Any]) -> bool:
    """A title that names nothing but the app (the Claude app is always "Claude")."""
    title = str(payload.get("title") or "").strip().lower()
    return not title or title == str(payload.get("app") or "").strip().lower()


def window_key(payload: Mapping[str, Any]) -> str:
    return f"{payload.get('app', '')}\t{str(payload.get('title') or '')[:120]}"


@dataclass(frozen=True)
class PlaceRule:
    """A location inside a named place that belongs to a domain."""

    places: tuple

    def match(self, obs: Observation) -> Attribution | None:
        from .places import place_at

        lat, lon = obs.payload.get("lat"), obs.payload.get("lon")
        if lat is None or lon is None:
            return None
        p = place_at(float(lat), float(lon), [x for x in self.places if x.domain])
        return Attribution(domain=p.domain) if p else None


@dataclass(frozen=True)
class SenderRule:
    """Email sender address or @domain suffix."""

    sender: str
    result: Attribution

    def match(self, obs: Observation) -> Attribution | None:
        sender = str(obs.payload.get("sender") or "").lower()
        pat = self.sender.lower()
        if sender and (sender == pat or (pat.startswith("@") and sender.endswith(pat))):
            return self.result
        return None


def rules_from_config(
    cfg: Mapping[str, Any],
    learned: Mapping[str, Mapping[str, Any]] = {},
    places: Sequence[Any] = (),
    known_titles: Mapping[str, Mapping[str, Any]] = {},
    shown_files: Mapping[str, str] = {},
) -> list[Rule]:
    """Builds rules from attribution.yaml. Learned (user-confirmed) rules come first. `shown_files`: the files deep
    talks showed the user (id → project), placed where their project is."""
    rules: list[Rule] = []
    for key, value in learned.items():
        kind, _, match = key.partition(":")
        if kind == "repo":
            rules.append(RepoRule(match, _split(value)))
        elif kind == "app":
            rules.append(AppRule(match, _split(value)))
    for repo, value in (cfg.get("repos") or {}).items():
        rules.append(RepoRule(str(repo), _split(value)))
    for entry in cfg.get("fields") or ():
        rules.append(FieldRule(str(entry["field"]), str(entry["equals"]), _split(entry)))
    for entry in cfg.get("windows") or ():
        title = entry.get("title")
        rules.append(
            WindowRule(
                app=entry.get("app"),
                title=re.compile(title) if title else None,
                result=_split(entry),
            )
        )
    for sender, value in (cfg.get("senders") or {}).items():
        rules.append(SenderRule(str(sender), _split(value)))
    # project names inside window titles (only names long enough to be specific)
    projects = {str(r): v for r, v in (cfg.get("repos") or {}).items()}
    projects.update({k.split(":", 1)[1]: v for k, v in learned.items() if k.startswith("repo:")})  # the user's answers win
    for name, value in projects.items():
        if len(name) >= 4:
            rules.append(TitleProjectRule(name, _split(value)))
    placed = {fid: _split(projects[p]) for fid, p in shown_files.items() if p in projects}
    if placed:
        rules.append(ShownFileRule(placed))
    if known_titles:
        rules.append(KnownTitleRule(dict(known_titles)))
    if places:
        rules.append(PlaceRule(tuple(places)))
    for lang, goal in (cfg.get("languages") or {}).items():
        rules.append(LanguageGoalRule(str(lang), str(goal)))
    return rules


class Attributor:
    def __init__(
        self,
        rules: Sequence[Rule],
        domain_sensitivity: Mapping[str, Sensitivity],
        sensitive_domains: Iterable[str],
    ):
        self._rules = list(rules)
        self._domain_sensitivity = dict(domain_sensitivity)
        self._sensitive = set(sensitive_domains)

    def known_domain(self, domain: str | None) -> bool:
        return domain is not None and (domain in self._domain_sensitivity or domain in self._sensitive)

    def sensitivity_for(self, domain: str | None, base: Sensitivity) -> Sensitivity:
        if domain is None or domain in self._sensitive:
            return Sensitivity.SENSITIVE
        return max(base, self._domain_sensitivity.get(domain, Sensitivity.SENSITIVE))

    def attribute(self, obs: Observation) -> Observation:
        domain = obs.domain if self.known_domain(obs.domain) else None
        goal = obs.goal
        for rule in self._rules:
            if domain is not None and goal is not None:
                break
            hit = rule.match(obs)
            if hit is None:
                continue
            if domain is None and hit.domain:
                domain = hit.domain
            # a goal comes along only with its own domain (or from a rule that
            # names no domain, like a language): a rule that lost the domain
            # to an earlier one does not get to add its goal
            if goal is None and hit.goal and (hit.domain is None or hit.domain == domain):
                goal = hit.goal
        if domain is not None and not self.known_domain(domain):
            domain = None
        return obs.with_attribution(domain, goal, self.sensitivity_for(domain, obs.sensitivity))
