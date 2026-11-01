"""Tools for an application packet Muninn prepared: set it aside, or say it was sent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from . import Outcome, Turn

if TYPE_CHECKING:
    from ..prepare import Packets


def packet_id(a: dict[str, Any], turn: Turn) -> int:
    """The packet named, or the one being discussed."""
    ref = a.get("packet") or ((turn.about or {}).get("ref") if (turn.about or {}).get("kind") == "packet" else None)
    try:
        return int(ref)
    except (TypeError, ValueError):
        raise ValueError(turn.words("tools_packets.which")) from None


class DismissPacket:
    name: ClassVar[str] = "dismiss_packet"
    description: ClassVar[str] = "The user decided not to apply, for now, to the opening of an application packet (nothing more is prepared for it). args: {packet: <packet id; may be left out for the one under discussion>, why: <one sentence>}"
    lookup: ClassVar[bool] = False

    def __init__(self, packets: Packets):
        self._packets = packets

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        rec = self._packets.dismiss(packet_id(a, turn))
        if rec is None:
            raise ValueError(turn.words("tools_packets.already_handled"))
        return Outcome(rec.text, action_id=rec.id)


class PacketSent:
    name: ClassVar[str] = "packet_sent"
    description: ClassVar[str] = "The user says an application packet has been sent (recorded as applied). args: {packet: <packet id; may be left out for the one under discussion>}"
    lookup: ClassVar[bool] = False

    def __init__(self, packets: Packets):
        self._packets = packets

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        rec = self._packets.done(packet_id(a, turn))
        if rec is None:
            raise ValueError(turn.words("tools_packets.already_handled"))
        return Outcome(rec.text, action_id=rec.id)
