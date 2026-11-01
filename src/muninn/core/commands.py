"""What the user types to Muninn: understood by a model (the cloud one when the
user allows it and the words are not about work, see understanding.py),
carried out by code.

The model only chooses tools and arguments from a fixed list, given the
current goals, targets and calendar blocks; every argument is checked against
those before anything happens. What can be undone is done and comes back
with an undo; nothing here sends anything to anyone. The user's words are
kept as a note (local, sensitive) for reflection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Collection, Sequence

from .answer import Answerer
from .dossier import Dossier
from .facts import ImpactAnalyzer
from .models import ALL_STATUSES, Attachment, DomainPlan
from .ports import Calendar, Clock
from .timewin import local, local_date
from .tools import FACT, Outcome, ToolRegistry, Turn
from .tools.builtin import BUILTIN
from .idea_next import NEXT_STEPS, IdeaNext
from .topic_memory import WINDOW, View
from .language import Language, written_in
from .reply_json import json_object

log = logging.getLogger(__name__)

# the built-in tools as the model reads them, and the lookups (results that are the answer); a
# runner's own registry may differ, these are what the routing cases were recorded against
TOOLS = {c.name: c.description for c in BUILTIN}
LOOKUPS = {c.name for c in BUILTIN if c.lookup}
ASKS = ("ask",)  # looking up the user's plans: not offered when the dossier of what is talked about is at hand

SYSTEM = """You are Muninn, the user's butler. Turn one sentence the user says into the actions to carry out.
Reply with one JSON object and nothing else:
{"actions": [{"tool": "<tool>", "args": {...}}], "reply": "<one sentence to the user, in {language}>",
 "for_project": "<only when there is beside_talk: one sentence to tell that project; otherwise null>",
 "suggest": [{"label": "<the words on the button, {label_length}>", "tool": "<tool>", "args": {...}}],
 "topic": {"title": "<what this conversation is about, the way the user would say it, {title_length}>", "state": "open | doing | closed", "now": "<where it stands now, one sentence>"},
 "question_for_him": "<the one thing reply leaves for the user to answer, decide or go do, as one sentence the user understands without reading the conversation; otherwise null>",
 "next": "<only when about is an idea: discuss | experiment | offer_experiment | promote | shelve>"}
Everything the user reads is in {language}: reply, for_project, the suggest labels, topic's title and now, question_for_him, and the text you write into tools' arguments for the user to read (titles, reasons, questions, facts).
Available tools:
%s
Rules:
- Ids (goal, target, deadline, block) all come from context.
- Work dates out from context's today (e.g. "tomorrow", "Saturday").
- context.calendar is the events on the user's own calendar (yesterday to a week ahead); context.blocks are the time slots Muninn scheduled. When the user asks whether the user is free or when the user has an appointment, look at calendar.
- When the user is only venting or noting something down (e.g. "So tired today"), actions may be empty and reply answers briefly.
- When there is conversation, it is the conversation you two are having: carry on with the topic and talk like a person; you may ask back, explain, give a view, and reply may run to 3 or 4 sentences.
- In a long conversation, conversation holds only the latest lines; what came before is in topic_state (where this matter stands so far: decided is what has been decided, open what is still awaited, next the next step, and where says which lines say it) and earlier (earlier lines word for word: the user's own, and the few related to this sentence; i is the line's number). When the two disagree, the original words in earlier win.
- other_topics are the other topics not yet closed (title and state): when the user speaks of "the other thing", settle_topic looks for it there.
- When there is beside_talk, the user is in a deep talk with that project, and this sentence is the part of what the user said there that the user handed to you; beside_talk.lines are that talk's latest lines (the user's, the project session's, Muninn's), there so you can tell what the user's "this" or "that one just now" refers to.
- Fill in for_project only when there is beside_talk: the part of your answer just now that this project's session needs to know next, as one sentence; nothing about the rest of the user's life, nothing about what you did. If this sentence corrects something you told the project earlier (the lines "Muninn told <project>" in beside_talk.lines), write it as a correction. Otherwise null.
- When the user wants to talk with a project or with an idea's sandbox, use talk_to_project or explore_idea.
- When about is an idea, next says what you do with it next (without waiting for the user to say so): experiment (there is something concrete to try: open its sandbox and try it right away; reply says what will be tried), offer_experiment (worth trying, but the user should nod first; reply may ask the user), promote (tried and it works, the user wants to keep going: worth making a real project), shelve (the user decided to set the idea aside for now), discuss (anything else: keep discussing). When the user has already said plainly what to do with the idea, put that in actions as the user said it, and write discuss in next.
- When there is sent_files, the user sent these files with this sentence (you can see the photos and PDFs): answer looking at them; when they are for a project's or an idea's experiment, use talk_to_project or explore_idea, and the files are handed over with it.
- When there is about, the user is discussing that item; until the user has taken a position, talk it over with the user in reply.
- When there is dossier, it is the full material of the part the user is discussing (plan, notes, job postings as written, prepared application packets, the user's résumés, project states): answer from it.
- When there is conversation or about, suggest holds 1 to 3 next steps the user most likely wants, going by the conversation so far (e.g. removing an application target, confirming or removing the item under discussion, changing a date), expressed as tools; these are not carried out: only when the user taps one.
- ask's question is a complete question that makes sense on its own: spell out what "this" and "that" in the conversation refer to.
- reply is the sentence the user sees after the actions are done; their results are shown separately below it.
- topic is the thing this conversation (this sentence included) is about: state is open (still being talked over), doing (you or a project is working on it), closed (nothing left to do: the user said fine, it is decided, the reminder is set); now says in one sentence where it stands (e.g. "Waiting for you to measure the wall", "Reminder tomorrow at 10:00").
- question_for_him is the one thing this conversation now waits on the user for, written as one complete sentence that spells out what "this" or "that one" refers to (e.g. "Have you measured the backyard wall? How long is it?", "Dentist tomorrow at 10:30: should I remind you beforehand?")."""


def system(words: Language, tools: str) -> str:
    """The routing prompt with its tool list, asking for what the user reads in `words`' language."""
    # how short a button or a title must be depends on how the language is written
    sized = SYSTEM.replace("{label_length}", words("commands.label_length")).replace("{title_length}", words("commands.title_length"))
    return written_in(sized, words) % tools


@dataclass
class CommandResult:
    reply: str
    outcomes: list[Outcome] = field(default_factory=list)
    understood: bool = True
    stood_in: bool = False  # a local model answered in place of the cloud
    suggest: list[dict[str, Any]] = field(default_factory=list)  # next steps offered as buttons, not done
    topic: dict[str, Any] | None = None  # what the conversation is about and where it stands, as the model put it
    question: str | None = None  # what it now waits on the user for, as one sentence; None when nothing
    made: dict[str, Any] | None = None  # the latest thing the round brought into being ({kind, id})


def parse(reply: str | None, names: set[str] | None = None, lookups: set[str] | None = None, *, words: Language) -> dict[str, Any] | None:
    """The model's routing reply, kept to the known tools (`names`, by default the built-in ones), its reply
    without the sentences that send the user to a slash command (cut as `words` cuts sentences)."""
    plan = read_route(reply, names, lookups)
    if plan is not None:
        plan["reply"] = without_slash(plan["reply"], words)
    return plan


def read_route(reply: str | None, names: set[str] | None = None, lookups: set[str] | None = None) -> dict[str, Any] | None:
    """The model's routing reply as it wrote it, kept to the known tools: what it chose to do, read the same in any
    language (the evals score this)."""
    names = set(TOOLS) if names is None else names
    lookups = LOOKUPS if lookups is None else lookups
    d = json_object(reply)
    if d is None:
        return None
    actions = [a for a in d.get("actions") or [] if isinstance(a, dict) and a.get("tool") in names]
    done = [(a["tool"], a.get("args") or {}) for a in actions]
    suggest = [{"label": str(x.get("label") or "")[:40], "tool": x["tool"], "args": x.get("args") if isinstance(x.get("args"), dict) else {}}
               for x in d.get("suggest") or []
               if isinstance(x, dict) and x.get("tool") in names and x["tool"] not in lookups | {FACT} and x.get("label")]
    suggest = [x for x in suggest if (x["tool"], x["args"]) not in done]  # offered: what was not done already
    tp = d.get("topic") if isinstance(d.get("topic"), dict) else {}
    state = {"waiting": "open"}.get(tp.get("state"), tp.get("state"))  # what waits on the user is question_for_him's to say
    topic = ({"title": str(tp.get("title") or "")[:40], "state": state, "now": str(tp.get("now") or "")[:80]}
             if state in TOPIC_STATES and tp.get("title") else None)
    question = str(d.get("question_for_him") or "").strip()[:200] or None
    for_project = str(d.get("for_project") or "").strip()[:FOR_PROJECT_MAX] or None
    return {"actions": actions[:4], "reply": str(d.get("reply") or "")[:600], "for_project": for_project,
            "suggest": suggest[:3], "topic": topic, "question": question, "next": d["next"] if d.get("next") in NEXT_STEPS else None}


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # the "today" line's weekday: prompt structure, the same in every language


def who(role: str) -> str:
    """Who said a line of the conversation, as the routing prompt reads it."""
    return "the user" if role == "user" else "you"


FOR_PROJECT_MAX = 300  # what Muninn tells a project's session of its answer beside the talk
TOPIC_STATES = ("open", "doing", "closed")  # what the model may say of a conversation; shelved is Muninn's own
# the slash commands the app still takes (typed by hand, as a fallback); never something to tell the user to type
SLASH = re.compile(r"(?<![\w/.:])/(attach|detach|work|discuss|terminal)\b")


def without_slash(reply: str, words: Language) -> str:
    """The reply without the sentences that send the user to a slash command (an earlier reply may have, and the
    model repeats what the conversation says): talking to a project or a sandbox is Muninn's to do, never the user's."""
    if not SLASH.search(reply):
        return reply
    return "".join(s for s in words.sentences(reply) if not SLASH.search(s)).strip()


class CommandContext:
    """What the command model sees besides the sentence: today, the goals, targets and manual
    deadlines it may name, Muninn's own calendar blocks and the user's calendar."""

    def __init__(self, clock: Clock, tz: str, plans: Callable[[bool], Sequence[DomainPlan]], calendar: Calendar | None,
                 is_work_line: Callable[[str], bool], calendar_view: Callable[[datetime, datetime, bool], Any], *, words: Language):
        """`plans(cloud)`: every plan, without the work domains for the cloud; `is_work_line`: whether a
        line reads as work (kept from the cloud); `calendar_view(start, end, cloud)`: the user's own calendar."""
        self.words = words
        self._clock = clock
        self._tz = tz
        self._plans = plans
        self._calendar = calendar
        self._is_work_line = is_work_line
        self._calendar_view = calendar_view

    def build(self, cloud: bool = False) -> dict[str, Any]:
        now = self._clock.now()
        today = local_date(now, self._tz)
        plans = self._plans(cloud)
        blocks = []
        if self._calendar is not None and self._calendar.available():
            for ev in self._calendar.own_events(now - timedelta(hours=1), now + timedelta(days=7)):
                if cloud and self._is_work_line(ev.title):
                    continue
                blocks.append({"id": ev.id, "title": ev.title.removeprefix("Muninn · "),
                               "when": f"{local(ev.start, self._tz):%Y-%m-%d %H:%M}"})
        return {
            "today": f"{today.isoformat()} ({WEEKDAYS[today.weekday()]}) {local(now, self._tz):%H:%M}",
            # every domain, a new one whose goals still wait for a yes included
            "domains": [{"id": p.id, "title": p.title} for p in plans],
            "goals":[{"id": f"{p.id}/{g.id}", "title": g.title, "metric": g.metric} for p in plans for g in p.goals],
            "targets": [{"id": f"{p.id}/{i.target}", "title": p.target_title(i.target), "status": i.status}
                        for p in plans for i in p.tracker],
            "statuses": ALL_STATUSES,
            "deadlines": [{"id": f"{p.id}/{d.id}", "title": d.title, "due": d.due.isoformat()}
                          for p in plans for d in p.deadlines if d.done_when.manual],
            "blocks": blocks,
            # the user's own calendars, yesterday to a week ahead (blocks above are Muninn's)
            "calendar": self._calendar_view(now - timedelta(days=1), now + timedelta(days=7), cloud),
        }


class CommandRunner:
    """Understands a sentence (which tools, with which arguments) and carries the round out."""

    def __init__(self, tools: ToolRegistry, context: CommandContext, understanding: Any, *,
                 sensitive_domains: Callable[[], Collection[str]], add_note: Callable[[str], Any],
                 dossier: Callable[[], Dossier], answerer: Callable[[], Answerer],
                 impact_analyzer: Callable[[], ImpactAnalyzer], apply_impact: Callable[..., list[Outcome]],
                 link_case: Callable[[str, int | None], None], lease_projects: Callable[[Sequence[str]], set[str]],
                 idea_next: IdeaNext | None = None, words: Language):
        """`understanding` asks the model (deciding first whether the cloud may read the turn);
        `add_note` keeps the user's words; `dossier`, `answerer` and `impact_analyzer` give the
        discussion's material, answers to questions and what news settles; `apply_impact(fact,
        impact, quote=, fyi=, only_to=)` acts on it; `link_case` ties what was done to the model
        call that decided it; `lease_projects` names the projects of the lent sessions content came from;
        `idea_next` adds what Muninn does next in a discussion of an idea."""
        self.words = words
        self.tools = tools
        self._context = context
        self._understanding = understanding
        self._sensitive = sensitive_domains
        self._add_note = add_note
        self._dossier = dossier
        self._answerer = answerer
        self._impact_analyzer = impact_analyzer
        self._apply_impact = apply_impact
        self._link_case = link_case
        self._lease_projects = lease_projects
        self._idea_next = idea_next

    def context(self, cloud: bool = False) -> dict[str, Any]:
        return self._context.build(cloud)

    # ── running ─────────────────────────────────────────────────────────────

    def understand(self, text: str, *, keep_note: bool = True, history: Sequence[dict[str, Any]] = (),
                   about: dict[str, Any] | None = None, talk: bool = False, view: View | None = None,
                   attachments: Sequence[Attachment] = (), marks: Collection[str] = (),
                   beside: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The slow part (the model); changes nothing but the note.

        `history` is the conversation so far (oldest first), `about` the item
        the user is discussing, `view` what of a long conversation is read (else
        its latest lines), `attachments` the files sent with it (the model sees them), `marks` what the
        conversation is marked with (a question asked once there), `beside` the deep talk this is Muninn's part
        of a sentence said in ({lease, project, talk}: the talk's latest lines, read only to tell what the words
        refer to; only that project hears of the round, and nothing in it sends the conversation elsewhere). The cloud reads the
        turn only when this sentence, every earlier one and the item are all clear of work."""
        text = text.strip()
        if not text and attachments:
            text = self.words("commands.sent_files", names=self.words.join(a.name for a in attachments))
        if not text:
            return None
        if keep_note:
            self._add_note(text)
        # a turn marked local_only (a lent session's summary, or a reply that may repeat it) is never cleared: the
        # round is sensitive (where it is read is the routing's to decide), and only that session's projects hear of it
        view = view or View(list(history[-WINDOW:]))
        state = view.state or {}
        local_turns = [t for t in view.shown() if t.get("local_only")] + ([state] if state.get("local_only") else []) + (
            [{"via": f"lease:{beside['lease']}"}] if beside else [])
        local = bool(local_turns)
        # which lent sessions that content came from ("lease:<id>"): only their projects may hear of this round
        via = sorted({v for t in local_turns for v in ([t["via"]] if isinstance(t.get("via"), str) else t.get("via") or [])})
        earlier_ok = all(t.get("cloud_ok") for t in history if t.get("role") == "user") and state.get("cloud_ok", True)
        about_ok = about is None or (about.get("domain") is not None
                                     and about["domain"] not in self._sensitive())
        cleared = self._understanding.clear(text) if earlier_ok and about_ok and not local else None
        data: dict[str, Any] = {"context": self.context(cloud=cleared is not None)}
        if view.state:
            data["topic_state"] = {k: state[k] for k in ("decided", "open", "next", "where") if k in state}
        if view.earlier:
            data["earlier"] = [{"i": t["i"], "who": who(t["role"]), "text": t["text"]} for t in view.earlier]
        if view.recent:
            data["conversation"] = [{"who": who(t["role"]), "text": t["text"]} for t in view.recent]
        # the other open topics, one line each; read in the cloud only when they were cleared themselves
        others = [{"title": o["title"], "state": o["state"]} for o in view.others if cleared is None or o.get("cloud_ok")]
        if others:
            data["other_topics"] = others
        if beside:  # Muninn's part of something said in a deep talk: that talk as it went last, to read the words by
            data["beside_talk"] = {"project": beside.get("called") or beside["project"], "lines": list(beside.get("talk") or [])}
        if about:
            data["about"] = {k: v for k, v in about.items() if k != "ref"}
            if (dossier := self._dossier().build(about.get("domain"))):
                data["dossier"] = dossier
        data["said"] = text
        if attachments:
            data["sent_files"] = [{"name": a.name, "type": a.media_type} for a in attachments]
        # what is being talked about comes with its whole dossier: it is answered from that, not looked up again
        without = ASKS if "dossier" in data else ()
        routed = self._understanding.complete("route", system(self.words, self.tools.tool_list(without)), data, cleared, json_mode=True,
                                              attachments=attachments)
        if routed.login_lost:  # nothing understood it, and nothing should guess in its place
            return {"actions": [], "suggest": [], "about": about, "case": routed.case, "cloud_ok": cleared is not None,
                    "local_only": local, "via": via, "talk": talk or bool(history or about), "stood_in": False,
                    "reply": self.words("commands.login_lost", who=routed.login_lost)}
        if routed.refused == "no_model":  # nothing set up to understand it yet: kept, and said why
            return {"actions": [], "suggest": [], "about": about, "case": routed.case, "cloud_ok": cleared is not None,
                    "local_only": local, "via": via, "talk": talk or bool(history or about), "stood_in": False,
                    "reply": self.words("commands.no_model"), "unanswered": True}
        if routed.refused == "budget":  # today's limit is reached: kept, and said why
            return {"actions": [], "suggest": [], "about": about, "case": routed.case, "cloud_ok": cleared is not None,
                    "local_only": local, "via": via, "talk": talk or bool(history or about), "stood_in": False,
                    "reply": self.words("commands.over_budget"), "unanswered": True}
        if routed.refused in ("capability", "routing"):  # nothing allowed to read the files was there to ask
            kinds = self.words.join(dict.fromkeys(self.words("commands.photos") if a.is_image else self.words("commands.pdfs")
                                                  for a in attachments if a.is_image or a.media_type == "application/pdf"))
            reply = (self.words("commands.files_local_only", kinds=kinds) if routed.refused == "routing"
                     else self.words("commands.files_unreadable", kinds=kinds))
            return {"actions": [], "suggest": [], "about": about, "case": routed.case, "cloud_ok": cleared is not None,
                    "local_only": local, "via": via, "talk": talk or bool(history or about), "stood_in": False,
                    "reply": reply, "unanswered": True}
        if routed.unanswered:  # no model answered; none guesses in its place
            return {"actions": [], "suggest": [], "about": about, "case": routed.case, "cloud_ok": cleared is not None,
                    "local_only": local, "via": via, "talk": talk or bool(history or about), "stood_in": False,
                    "reply": self.words("commands.unanswered"), "unanswered": True}
        plan = parse(routed.reply, self.tools.names - set(without), self.tools.lookups, words=self.words)
        if plan is not None:
            plan["case"], plan["about"], plan["cloud_ok"] = routed.case, about, cleared is not None
            plan["stood_in"] = routed.stood_in
            plan["local_only"], plan["via"] = local, via
            plan["talk"] = talk or bool(history or about)
        # news, or (outside a conversation) something said in passing: see what it settles across every open item
        in_talk = talk or bool(history or about)
        if plan is not None and (any(a["tool"] == FACT for a in plan["actions"]) or (not plan["actions"] and not in_talk)):
            fact = next((str((a.get("args") or {}).get("fact") or text) for a in plan["actions"] if a["tool"] == FACT), text)
            plan["impact"] = self._impact_analyzer().analyze(fact, cleared)
            plan["fact"] = fact
            plan["quote"] = text
            plan["actions"] = [a for a in plan["actions"] if a["tool"] != FACT]
        if plan:
            if not beside:  # only a round beside a deep talk has a project to tell
                plan["for_project"] = None
            for a in plan["actions"]:
                if a["tool"] == "ask":  # the slow lookup also happens here, outside the engine lock
                    answered = self._answerer().reply(str((a.get("args") or {}).get("question") or text), cleared, attachments=attachments)
                    a["answer"] = answered.reply
                    plan["stood_in"] = plan["stood_in"] or answered.stood_in
            if beside:  # beside a deep talk: nothing done here sends the conversation anywhere else
                plan["actions"] = [a for a in plan["actions"] if "conversation" not in self.tools.acts_on(a["tool"])]
                plan["suggest"] = [s for s in plan.get("suggest") or [] if "conversation" not in self.tools.acts_on(s["tool"])]
                plan.pop("next", None)
                # what the project is told of the answer: never from a round that did something for the user (its
                # sentence may carry what was done: a tool, or news that settles or goes to something), nor one that
                # read another project's talk
                impact = plan.get("impact")
                acted = any(a["tool"] not in self.tools.lookups for a in plan["actions"]) or bool(
                    impact is not None and (impact.effects or impact.projects))
                if acted or self._lease_projects(via) != {beside["project"]}:
                    plan["for_project"] = None
            elif self._idea_next is not None:  # an idea moves forward without being told
                acted = any({"idea", "conversation"} & set(self.tools.acts_on(a["tool"])) for a in plan["actions"])
                self._idea_next.apply(plan, text, acted=acted, marks=marks)
            else:
                plan.pop("next", None)
        return plan

    def execute(self, plan: dict[str, Any] | None) -> CommandResult:
        if plan is None:
            return CommandResult(self.words("commands.not_understood"), understood=False)
        outcomes = []
        # a round carrying a lent session's content: only that lease's projects may hear what it says
        only_to = self._lease_projects(plan.get("via") or []) if plan.get("local_only") else None
        lookups_only = bool(plan["actions"]) and all(a["tool"] in self.tools.lookups for a in plan["actions"])
        made = plan.get("made")  # what the conversation brought into being before; the actions below may add to it
        for a in plan["actions"]:
            try:
                turn = Turn(about=plan.get("about"), only_to=only_to, answer=a.get("answer"), made=made, thread=plan.get("thread"),
                            via=tuple(plan.get("via") or ()) if only_to is not None else (), words=self.words)
                o = self.tools.run(a["tool"], dict(a.get("args") or {}), turn)
                made = o.made or made
                outcomes.append(o)
            except (KeyError, ValueError, TypeError) as err:
                outcomes.append(Outcome(self.words("commands.not_done", why=err), failed=True))
        # for lookups the result is the answer; the model's sentence tends to narrate ("let me look that up"). When something
        # was not done, its sentence (written before) may say it was: only what happened is said
        reply = "" if lookups_only or any(o.failed for o in outcomes) else plan["reply"]
        for o in outcomes:  # what the routing call did, so an undo or a "no" counts against it
            self._link_case(f"action:{o.action_id}", plan.get("case") if o.action_id else None)
            self._link_case(f"confirm:{o.confirm_id}", plan.get("case") if o.confirm_id else None)
        if plan.get("impact") is not None:
            # in a discussion only the items a fact settles move; no FYI to every project
            impact = self._apply_impact(plan["fact"], plan["impact"], quote=plan.get("quote"), fyi=not plan.get("talk"),
                                         only_to=self._lease_projects(plan["via"]) if plan.get("local_only") else None)
            for o in impact:  # what the fact analysis decided
                self._link_case(f"confirm:{o.confirm_id}", plan["impact"].case_id if o.confirm_id else None)
                self._link_case(f"handoff:{o.handoff_id}", plan["impact"].case_id if o.handoff_id else None)
            outcomes += impact
        return CommandResult(reply or (self.words("commands.noted") if not outcomes and not plan.get("tapped") else ""), outcomes,
                             suggest=list(plan.get("suggest") or []), stood_in=bool(plan.get("stood_in")), made=made,
                             topic=plan.get("topic"), question=plan.get("question"), understood=not plan.get("unanswered"))

    def run(self, text: str) -> CommandResult:
        return self.execute(self.understand(text))
