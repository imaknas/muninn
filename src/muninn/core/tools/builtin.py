"""Muninn's own tools, in the order the model reads them."""

from __future__ import annotations

from .blocks import CancelBlock, MoveBlock, Schedule
from .conversation import AnswerQuestion, Ask, AskProject, FindMail, RecordFact, RequestFeature, TalkToProject, Today, UndoAction
from .plan import ChangePlan, DecideItem, ProposeDomain
from .records import LogHours, MarkDone, Progress, SetStatus
from .packets import DismissPacket, PacketSent
from .apply import AnswerForm
from .places import NamePlace
from .alerts import AlertSeen
from .attribution import AssignSources
from .ideas import CaptureIdea, ExploreIdea, PromoteIdea, RenameIdea, SettleIdea
from .tasks import AddTask, CloseTask, SetTaskDue
from .topics import SettleTopic

# the order is the tool list the model reads (and the routing cases recorded so far were judged on)
BUILTIN = (SetStatus, LogHours, MarkDone, Progress, Schedule, MoveBlock, CancelBlock, FindMail, Today, Ask,
           ChangePlan, RecordFact, DecideItem, AnswerQuestion, AddTask, CloseTask, SetTaskDue, RequestFeature,
           DismissPacket, PacketSent, AnswerForm, NamePlace, AlertSeen, CaptureIdea, SettleIdea, ExploreIdea, PromoteIdea, RenameIdea, ProposeDomain, AssignSources,
           UndoAction, SettleTopic, TalkToProject, AskProject)
ORDER = tuple(c.name for c in BUILTIN)
