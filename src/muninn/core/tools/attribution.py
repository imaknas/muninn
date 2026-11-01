"""Tools for what counts toward a domain: the user says a repo, an app or a named place belongs to one."""

from __future__ import annotations

from typing import Any, Callable, ClassVar, Sequence

from ..actions import ActionGate, DecidedBy, SourcesAssigned
from . import Outcome, Turn


def _names(x: Any) -> tuple[str, ...]:
    items = x if isinstance(x, (list, tuple)) else [x] if x else []
    return tuple(dict.fromkeys(str(i).strip() for i in items if str(i).strip()))


class AssignSources:
    name: ClassVar[str] = "assign_sources"
    description: ClassVar[str] = ("The user says which repos, apps or places count their time toward a domain (e.g. \"the muninn repo counts as side-project\", \"time in Blender counts as 3D\", "
                                  "\"the pottery studio counts as pottery\"). args: {domain: <domain id>, repos: [<repo name>], apps: [<app name>], "
                                  "places: [<places the user has named in Muninn>]}")
    lookup: ClassVar[bool] = False

    def __init__(self, domains: Callable[[], Sequence[tuple[str, str]]], place_names: Callable[[], Sequence[str]], gate: ActionGate):
        """`domains`: (id, title) of every domain; `place_names`: the places named in the app (only those can be given a domain)."""
        self._domains = domains
        self._place_names = place_names
        self._gate = gate

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        titles = dict(self._domains())
        domain = str(a.get("domain") or (turn.about or {}).get("domain") or "").split("/")[0]
        if domain not in titles:
            raise ValueError(turn.words("tools_attribution.which_domain"))
        repos, apps, places = _names(a.get("repos")), _names(a.get("apps")), _names(a.get("places"))
        named = set(self._place_names())
        unknown = [p for p in places if p not in named]
        if unknown:
            raise ValueError(turn.words("tools_attribution.unnamed_places", places=turn.words.join(unknown), first=unknown[0]))
        if not (repos or apps or places):
            raise ValueError(turn.words("tools_attribution.which_sources"))
        r = self._gate.run(SourcesAssigned(domain, titles[domain], repos, apps, places), DecidedBy.USER)
        if r is None:
            raise ValueError(turn.words("tools_attribution.not_kept"))
        return Outcome(turn.words("tools_attribution.assigned", done=r.text), action_id=r.id)


class AttributionTools:
    """What counts toward a domain, said in words."""

    def __init__(self, domains: Callable[[], Sequence[tuple[str, str]]], place_names: Callable[[], Sequence[str]], gate: ActionGate):
        self._domains = domains
        self._place_names = place_names
        self._gate = gate

    def tools(self) -> list[Any]:
        return [AssignSources(self._domains, self._place_names, self._gate)]
