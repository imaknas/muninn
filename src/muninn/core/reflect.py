"""Reflection on what the user said, from conversation text.

Two steps; conversation text is always sensitive, so both run on the local
model unless routing sends everything to the cloud:

1. digest: each session's new turns become a small structured record
   (summary, commitments, decisions, themes, mood, friction). Run nightly
   under a time budget, newest sessions first.
2. weekly: the week's records are counted in code (what keeps coming up,
   what was promised, how the mood moved), then the model writes a
   short reflection from those counts. It holds up a mirror and ends with one
   question; it does not grade.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta
from typing import Any, Callable, Protocol, Sequence
from zoneinfo import ZoneInfo

from .attribution import Attributor
from .llm_router import LLMRequest, LLMRouter
from .metrics import PASSIVE_ACTIVITY_KINDS, estimate_hours, night_window, span_of
from .models import InboxItem, Observation, Sensitivity, Turn
from .ports import Clock, InboxStore, NoProjectRecords, ObservationStore, ProjectRecords, StateStore, TranscriptSource, TurnStore
from .timewin import iso_week_window, period_window
from .language import Language, written_in
from .reply_json import json_object

log = logging.getLogger(__name__)

MOODS = ["calm", "focused", "excited", "anxious", "tired", "frustrated", "hesitant"]
# the moods digests were kept with before they were ids: read as the id each one was
OLD_MOODS = dict(zip(["平穩", "專注", "興奮", "焦慮", "疲累", "挫折", "猶豫"], MOODS))
# how a model may start the summary, echoing the prompt's "<one sentence: …>" (in whichever language it writes)
SUMMARY_ECHOES = ("one sentence:", "一句話:", "一句話：")
EXCERPT_CHARS = 14_000
STATE_CHARS = 2_500
ASSISTANT_CHARS = 300

DIGEST_SYSTEM = f"""You read one conversation between the user and an AI assistant and record what it shows about the user.
Reply with one JSON object and nothing else, all text in {{language}}:
{{"summary": "<one sentence: what the user was trying to do>",
 "domain": "<one of the given domain ids, or null>",
 "commitments": [{{"text": "something the user said they would do but have not finished in this conversation", "scope": "personal or project"}}],
 "decisions": [{{"what": "a decision the user made", "why": "the reason the user gave"}}],
 "themes": ["2 to 4 short topic words"],
 "mood": "one of {MOODS}",
 "friction": ["what the user got stuck on, resisted, went back and forth on or complained about (an empty array if none)"]}}
scope: "project" for work on the project itself (code, docs, releases, the project's own tasks); "personal" for promises about the user's own life and habits (sleep, health, study time, applying for jobs, calling someone, decisions about career or money).
Only use what the user said or clearly agreed to."""


def digest_system(words: Language) -> str:
    return written_in(DIGEST_SYSTEM, words)


def weekly_system(words: Language) -> str:
    """The weekly reflection's prompt; its headings are what the user reads, in `words`' language."""
    def h(key: str) -> str:
        return words(f"reflect.heading_{key}")

    return f"""You are the user's personal reflection partner. From the JSON provided (statistics and records drawn from the user's conversations with AI assistants this week),
write a short weekly reflection in {words.name}, like a mirror: no grading, no lecturing, no pleasantries. Format:
{h("thinking")}: 2 to 3 sentences naming the topics that came up most and whether they match the user's priorities.
{h("promised")}: pick the 3 most important from personal_commitments (only the user's promises to themselves), and point out which the user has said more than once.
{h("decided")}: project decisions may only quote what project_states says; do not infer them from the conversations yourself. Then add decisions_outside_projects.
{h("time")}: if there is conversation_rhythm or observed_hours_by_domain, describe it in a sentence or two, quoting the numbers directly. When there are hours_at_places and activity_by_place, point out where the user gets which things done (e.g. how much PhD work at home). activity_by_place is the hours of computer activity while the user was at that place: say "what was done" just as it says.
{h("state")}: how the user's mood changed, and whether that has to do with the user's routine or with being stuck (only from the data: with no evidence, infer nothing).
{h("question")}: leave one open question worth the user's thinking about.
Use only the information in the JSON."""


def parse_digest(reply: str | None, domains: set[str]) -> dict[str, Any] | None:
    d = json_object(reply)
    if d is None or not d.get("summary"):
        return None
    summary = str(d["summary"]).strip()
    for prefix in SUMMARY_ECHOES:
        if summary.lower().startswith(prefix):
            summary = summary[len(prefix):].strip()
    out = {
        "summary": summary[:300],
        "domain": d.get("domain") if d.get("domain") in domains else None,
        "commitments": [c for c in (_commitment(x) for x in d.get("commitments") or []) if c][:8],
        "decisions": [
            {"what": str(x.get("what", ""))[:200], "why": str(x.get("why", ""))[:200]}
            for x in d.get("decisions") or [] if isinstance(x, dict) and x.get("what")
        ][:6],
        "themes": [str(x)[:30] for x in d.get("themes") or [] if x][:4],
        "mood": mood_of(d.get("mood")),
        "friction": [str(x)[:200] for x in d.get("friction") or [] if x][:6],
    }
    return out


def mood_of(said: Any) -> str | None:
    """A mood as an id, a kept one read as the id it was; None when it is none of them."""
    said = OLD_MOODS.get(said, said) if isinstance(said, str) else None
    return said if said in MOODS else None


# What a digest may carry out of Muninn: the model's own summary of a
# session, never the conversation text. Named one by one, so a field added
# to digests later stays inside until it is added here.
EXPORT_FIELDS = ("summary", "themes", "mood", "commitments", "decisions", "friction", "user_turns")


def export_digests(
    digests: Sequence[dict[str, Any]], tz: str, withhold: Callable[[dict[str, Any]], bool]
) -> tuple[list[dict[str, Any]], int]:
    """The digests that may leave, in a fixed shape, and how many were
    withheld (work and unattributed sessions stay inside)."""
    out, withheld = [], 0
    for d in digests:
        if withhold(d):
            withheld += 1
            continue
        last = d["until"].astimezone(ZoneInfo(tz))
        out.append({"session": d["session"], "project": d.get("project"), "domain": d.get("domain"),
                    "date": last.date().isoformat(), "last_turn": last.isoformat(),
                    **{k: d[k] for k in EXPORT_FIELDS if k in d}, **({"mood": mood_of(d["mood"])} if "mood" in d else {})})
    return out, withheld


def _commitment(x: Any) -> dict[str, str] | None:
    if isinstance(x, str) and x.strip():
        return {"text": x.strip()[:200], "scope": "personal"}
    if isinstance(x, dict) and str(x.get("text", "")).strip():
        return {"text": str(x["text"]).strip()[:200], "scope": "project" if x.get("scope") == "project" else "personal"}
    return None


def excerpt(turns: Sequence[Turn]) -> str:
    """The user's words in full, the assistant's trimmed; the most recent part
    when the session is long."""
    lines = []
    for t in turns:
        text = t.text if t.role == "user" else t.text[:ASSISTANT_CHARS] + ("…" if len(t.text) > ASSISTANT_CHARS else "")
        lines.append(f"[{'user' if t.role == 'user' else 'assistant'}] {text}")
    out = "\n\n".join(lines)
    return out[-EXCERPT_CHARS:]


@dataclass(frozen=True)
class WeekFacts:
    """Behaviour numbers from observation, to set beside what was said."""

    domain_hours: dict[str, float]
    late_minutes: float
    place_hours: dict[str, float] = field(default_factory=dict)
    # place → domain → hours of observed activity while there
    activity_by_place: dict[str, dict[str, float]] = field(default_factory=dict)


def aggregate(
    digests: Sequence[dict[str, Any]],
    domain_titles: dict[str, str],
    tracked_projects: set[str] = frozenset(),
) -> dict[str, Any]:
    """Counts in code. Work on a project that keeps its own records belongs to
    those records: its commitments and decisions are left out here (the
    project states stand in for its decisions)."""

    def tracked(d: dict[str, Any]) -> bool:
        return d.get("project") in tracked_projects

    themes = Counter(t for d in digests for t in d.get("themes", []))
    moods = Counter(m for d in digests if (m := mood_of(d.get("mood"))))
    by_domain = Counter(domain_titles.get(d.get("domain"), "other") for d in digests)
    personal = Counter(
        c["text"] for d in digests for c in d.get("commitments", [])
        if isinstance(c, dict) and (c.get("scope") == "personal" or not tracked(d))
    )
    return {
        "sessions": len(digests),
        "themes": themes.most_common(8),
        "moods": moods.most_common(),
        "sessions_by_domain": by_domain.most_common(),
        "personal_commitments": [c for c, _ in personal.most_common(12)],
        "repeated_commitments": [c for c, n in personal.items() if n > 1],
        "decisions_outside_projects": [x for d in digests if not tracked(d) for x in d.get("decisions", [])][:12],
        "active_tracked_projects": [p for p, _ in Counter(d["project"] for d in digests if tracked(d)).most_common(6)],
        "friction": [f for d in digests for f in d.get("friction", [])][:12],
        "summaries": [d["summary"] for d in digests][-25:],
    }


class Reflector:
    def __init__(self, store: TurnStore, router: LLMRouter, clock: Clock, *, monotonic: Callable[[], float] = time.monotonic):
        """`router` may be a dedicated one (e.g. a faster local model for digests)."""
        self._store = store
        self._router = router
        self._clock = clock
        self._monotonic = monotonic

    def _ask(self, system: str, content: str, json_mode: bool, task: str) -> str | None:
        # conversation text: sensitive and unattributed; local unless the router's policy is cloud-first
        return self._router.complete(LLMRequest(system, content, Sensitivity.SENSITIVE, None, json_mode=json_mode, task=task))

    def digest(
        self,
        domains: Sequence[tuple[str, str]],
        budget_s: float = 3600,
        max_sessions: int = 500,
        *,
        deadline: float | None = None,
        reserve_s: float = 180,
        project_domain: Callable[[str | None], str | None] = lambda _: None,
        words: Language,
    ) -> int:
        """Digests until the deadline. `reserve_s` is the longest one model call
        may take: a new session is started only if it can finish in time, so
        the run never overshoots. A project the attribution rules know keeps
        that domain whatever the model says."""
        deadline = deadline if deadline is not None else self._monotonic() + budget_s
        ids = {d for d, _ in domains}
        done = 0
        for s in self._store.sessions_to_digest(max_sessions):
            if self._monotonic() + reserve_s > deadline:
                break
            turns = self._store.turns(s["session"], s["after"], s["until"])
            if not any(t.role == "user" for t in turns):
                continue
            prompt = json.dumps({"domains": [{"id": d, "title": t} for d, t in domains], "project": s["project"],
                                 "conversation": excerpt(turns)}, ensure_ascii=False)
            data = parse_digest(self._ask(digest_system(words), prompt, True, "reflect_digest"), ids)
            if data is None:
                log.warning("digest failed for session %s; will retry next run", s["session"][:8])
                continue
            known = project_domain(s["project"])
            if known:
                data["domain"] = known
            data["user_turns"] = sum(1 for t in turns if t.role == "user")
            self._store.save_digest(s["session"], s["project"], s["until"], data, self._clock.now())
            done += 1
        return done

    def weekly(
        self,
        since: datetime,
        until: datetime,
        domain_titles: dict[str, str],
        facts: WeekFacts | None,
        now: datetime,
        records: ProjectRecords,
        iso_week: str | None = None,
        *,
        words: Language,
    ) -> InboxItem | None:
        digests = self._store.digests(since, until)
        if not digests:
            return None
        agg = aggregate(digests, domain_titles, records.projects())
        agg["project_states"] = {
            p: state[:STATE_CHARS] for p in agg["active_tracked_projects"] if (state := records.state(p))
        }
        stats = records.weekly_stats(iso_week) if iso_week else None
        if stats:
            agg["conversation_rhythm"] = {k: stats[k] for k in ("totals", "rhythm") if k in stats}
        if facts:
            agg["observed_hours_by_domain"] = {domain_titles.get(k, k): round(v, 1) for k, v in facts.domain_hours.items()}
            agg["late_night_minutes"] = round(facts.late_minutes)
            if facts.place_hours:
                agg["hours_at_places"] = {k: round(v, 1) for k, v in facts.place_hours.items()}
                agg["activity_by_place"] = {
                    place: {domain_titles.get(d, d): round(h, 1) for d, h in by.items()}
                    for place, by in facts.activity_by_place.items()
                }
        text = self._ask(weekly_system(words), json.dumps(agg, ensure_ascii=False), False, "reflect_week")
        if not text:
            log.warning("weekly reflection: no model answered, will retry")
            return None
        last = until - timedelta(seconds=1)
        return InboxItem(
            id=None,
            ts=now,
            domain="muninn",
            kind="reflection",
            title=words("reflect.weekly_title", since=since, last=last),
            body=text.strip(),
            data={"aggregate": {k: v for k, v in agg.items() if k != "project_states"}},
        )


class ReflectionStorage(TurnStore, StateStore, ObservationStore, InboxStore, Protocol):
    """Conversation text and its digests, the transcript cursor, the week's activity, and the preview's inbox item."""


class Reflection:
    """Reading conversation text and digesting it at night, the week's digests for the user's own
    reflection elsewhere, and Muninn's weekly reflection. Conversation text stays on the machine."""

    def __init__(self, store: ReflectionStorage, clock: Clock, tz: str, transcripts: TranscriptSource | None,
                 reflector: Reflector | None, domains: Callable[[], Sequence[tuple[str, str]]],
                 attributor: Callable[[], Attributor], sensitive: Callable[[], Any],
                 where_and_what: Callable[[datetime, datetime], tuple[dict[str, float], dict[str, dict[str, float]]]],
                 records: Callable[[], ProjectRecords], once: Callable[[str, datetime, Callable[[], InboxItem]], list[InboxItem]],
                 *, monotonic: Callable[[], float] = time.monotonic, words: Language):
        """`sensitive`: the sensitive domains; `where_and_what`: hours per place and per domain there;
        `once(marker, now, build)`: a scheduled report built, stored and pushed at most once per marker."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self.transcripts = transcripts
        self.reflector = reflector
        self._domains = domains
        self._attributor = attributor
        self._sensitive = sensitive
        self._where_and_what = where_and_what
        self._records = records
        self._once = once
        self._monotonic = monotonic

    def collect_conversations(self) -> int:
        if self.transcripts is None:
            return 0
        key = "cursor:transcript_text"
        turns, cursor = self.transcripts.collect(self._store.get_state(key), self._clock.now())
        n = self._store.add_turns(turns)
        self._store.set_state(key, cursor)
        return n

    def digest(self, budget_s: float = 3600, reserve_s: float = 180) -> tuple[int, int]:
        """Nightly: read new conversation text, then digest sessions. The whole
        run, reading included, ends within `budget_s` (other scheduled jobs
        wait for it)."""
        deadline = self._monotonic() + budget_s
        new = self.collect_conversations()
        if self.reflector is None:
            return new, 0
        done = self.reflector.digest(self._domains(), deadline=deadline, reserve_s=reserve_s,
                                     project_domain=self.project_domains(), words=self.words)
        return new, done

    def project_domains(self) -> Callable[[str | None], str | None]:
        attr = self._attributor()

        def project_domain(project: str | None) -> str | None:
            if not project:
                return None
            return attr.attribute(Observation("reflect", self._clock.now(), "probe", "probe", {"repo": project})).domain

        return project_domain

    def week_digests(self, week: str) -> dict[str, Any]:
        """The session digests of one ISO week, for the user's own weekly
        reflection elsewhere: the model's summaries only, never the
        conversation text. Sessions in a sensitive domain, by the digest or
        by today's attribution of its project, and sessions in no domain
        are withheld and only counted."""
        since, until = iso_week_window(week, self._tz)
        project_domain = self.project_domains()
        sensitive = self._sensitive()

        digests = [{**d, "domain": project_domain(d.get("project")) or d.get("domain"), "model_domain": d.get("domain")}
                   for d in self._store.digests(since, until)]
        sessions, withheld = export_digests(
            digests, self._tz, lambda d: d["domain"] is None or d["domain"] in sensitive or d["model_domain"] in sensitive)
        waiting = sum(1 for s in self._store.sessions_to_digest(10_000)
                      if s["until"] >= since and (s["after"] is None or s["after"] < until))
        return {"week": week, "since": since.isoformat(), "until": until.isoformat(),
                "complete": self._clock.now() >= until and waiting == 0,
                "sessions": sessions, "withheld": withheld, "undigested_sessions": waiting}

    def week_facts(self, since: datetime, until: datetime) -> WeekFacts:
        obs = self._store.observations(since, until, kinds=PASSIVE_ACTIVITY_KINDS)
        by_domain: dict[str, list] = {}
        for o in obs:
            if o.domain:
                by_domain.setdefault(o.domain, []).append(span_of(o))
        hours = {d: estimate_hours(sp, timedelta(minutes=20), timedelta(minutes=10)) for d, sp in by_domain.items()}
        late = 0.0
        day = since
        while day < until:
            n_start, n_end = night_window(day.replace(hour=12), self._tz, clock_time(23, 30), clock_time(6, 0))
            spans = [(max(a, n_start), min(b, n_end)) for a, b in map(span_of, obs) if a < n_end and b > n_start]
            late += estimate_hours(spans, timedelta(minutes=20), timedelta(minutes=10)) * 60 if spans else 0
            day += timedelta(days=1)
        place_hours, by_place = self._where_and_what(since, until)
        return WeekFacts(hours, late, place_hours, by_place)

    def weekly(self, *, preview: bool = False) -> list[InboxItem]:
        """The reflection on the last complete week (Monday to Monday), once
        per week; run it after that week's statistics exist. A preview covers
        this week so far, is stored without a push, and uses nothing up."""
        if self.reflector is None:
            return []
        now = self._clock.now()
        this_monday, _ = period_window("week", now, self._tz)
        since, until = (this_monday, now) if preview else (this_monday - timedelta(days=7), this_monday)
        iso_year, iso_week, _ = since.isocalendar()
        titles = dict(self._domains())

        def build() -> InboxItem | None:
            return self.reflector.weekly(
                since, until, titles, self.week_facts(since, until), now, self._records(), f"{iso_year}-W{iso_week:02d}",
                words=self.words,
            )

        if preview:
            item = build()
            if item is None:
                return []
            item = InboxItem(None, item.ts, item.domain, item.kind, self.words("reflect.preview", title=item.title), item.body, item.data)
            return [self._store.add_inbox(item)]

        def required() -> InboxItem:
            item = build()
            if item is None:
                raise RuntimeError("no digests for that week, or no model answered")
            return item

        try:
            return self._once(f"reflection:{since.date().isoformat()}", now, required)
        except RuntimeError as e:
            log.warning("weekly reflection skipped: %s", e)
            return []
