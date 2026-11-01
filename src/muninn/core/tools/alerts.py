"""Tools for Muninn's reminders: say one was seen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from . import Outcome, Turn

if TYPE_CHECKING:
    from ..alerts import Alerts


class AlertSeen:
    name: ClassVar[str] = "alert_seen"
    description: ClassVar[str] = "The user says the user has seen or knows about a reminder (no need to show it again). args: {alert: <reminder id; may be left out for the one under discussion>, all: <true means every reminder has been seen>}"
    lookup: ClassVar[bool] = False

    def __init__(self, alerts: Alerts):
        self._alerts = alerts

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        if a.get("all"):
            shown = self._alerts.current()
            ids = [i for x in shown for i in (x.get("ids") or [x["id"]])]
            title = turn.words("tools_alerts.count", n=len(shown))
        else:
            ref = a.get("alert") or ((turn.about or {}).get("ref") if (turn.about or {}).get("kind") == "alert" else None)
            try:
                x = self._alerts.find(int(ref))
            except (TypeError, ValueError):
                x = None
            if x is None:
                raise ValueError(turn.words("tools_alerts.which"))
            ids, title = x.get("ids") or [x["id"]], x["title"]
        r = self._alerts.seen(ids, title)
        if r is None:
            raise ValueError(turn.words("tools_alerts.none"))
        return Outcome(r.text, action_id=r.id)
