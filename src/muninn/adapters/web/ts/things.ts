// Today's things: everything waiting on the user (a question, a plan item, a project asking, a packet to send…)
// and what is being worked on (a deep talk), each knowing its card and what a discussion of it is about.
// The panel lists them; the conversation shows the first ones; the pill counts them.

import { api } from "./api.js";
import { el, kids, type Child } from "./dom.js";
import { called, PROVIDER, sender, WEEKDAY } from "./format.js";
import { confirmationCard, leaseCard, projectQuestionCard, questionCard, relinkCard, reviewCard, talkQuestionsCard, taskRow } from "./cards.js";
import { renderPacket } from "./pages.js";
import { updateBadges } from "./shell.js";
import { openTalk, permissionCard } from "./talk.js";
import { w, type Words } from "./words.js";
import type { About, ActiveLease, Confirmation, Home, List, PermissionRequest, ProjectQuestion, ReviewItem, TopicState, Waiting } from "./types.js";

export interface Data {
  home: Home; review: ReviewItem[]; conf: Confirmation[]; asks: ProjectQuestion[]; perms: PermissionRequest[]; lent: ActiveLease[];
  topics: TopicState[]; waiting: Waiting[];
}

export async function loadData(): Promise<Data> {
  const [home, review, conf, asks, perms, lent, topics, waiting] = await Promise.all([api<Home>("/api/home"), api<List<ReviewItem>>("/api/review"),
    api<List<Confirmation>>("/api/confirmations"),
    api<List<ProjectQuestion>>("/api/project-questions").catch(() => ({ items: [] as ProjectQuestion[] })),
    api<List<PermissionRequest>>("/api/permission-requests/pending").catch(() => ({ items: [] as PermissionRequest[] })),
    api<{ active?: ActiveLease[] }>("/api/leases?active=1").catch(() => ({ active: [] as ActiveLease[] })),
    api<List<TopicState>>("/api/topics").catch(() => ({ items: [] as TopicState[] })),
    api<List<Waiting>>("/api/waiting")]);
  updateBadges(home.unread);
  return { home, review: review.items, conf: conf.items, asks: asks.items, perms: perms.items, lent: lent.active || [], topics: topics.items,
           waiting: waiting.items };
}

type Kind = keyof Words["things"]["kinds"];
const isKind = (k: string): k is Kind => k in w.things.kinds;

export interface Thing {
  id: string;
  kind: Kind;
  group: "wait" | "doing";  // waiting on the user, or being worked on
  title: string;
  state: string;  // one line: what it is and where it stands
  card: () => Child | Promise<Child>;
  about?: About;  // what a discussion of it is about
}

const dayOf = (due: string): Date => new Date(`${due.slice(0, 10)}T00:00`);  // local midnight of a due date
const midnight = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const daysUntil = (due: string, now: Date): number => Math.round((dayOf(due).getTime() - midnight(now).getTime()) / 864e5);

export function dueLabel(due: string | null, now: Date = new Date()): string {
  if (!due) return "";
  const d = dayOf(due);
  const days = daysUntil(due, now);
  const md = w.things.monthDay(d);
  if (days < 0) return `${md} · ${-days === 1 ? w.things.yesterday : w.things.daysAgo(-days)}`;
  if (days === 0) return `${md} · ${w.things.today}`;
  if (days === 1) return `${md} · ${w.things.tomorrow}`;
  return days < 7 ? `${md} · ${w.things.weekday(WEEKDAY[d.getDay()] ?? "")}` : md;
}

// what the server lists, in its order: each entry drawn from the object it names (one not loaded yet is left for the
// next read)
export function things(d: Data): Thing[] {
  const h = d.home;
  const now = new Date(h.now);
  const out: Thing[] = [];
  for (const wt of d.waiting) {
    const t = draw(d, wt, now);
    if (t) out.push({ group: wt.group, ...t });
  }
  return out;
}

type Drawn = Omit<Thing, "group">;

function draw(d: Data, wt: Waiting, now: Date): Drawn | null {
  const ref = wt.id.slice(wt.id.indexOf(":") + 1);
  const h = d.home;
  switch (wt.id.slice(0, wt.id.indexOf(":"))) {
    case "relink":
      return { id: wt.id, kind: "relink", title: w.things.needsRelink(PROVIDER[ref] || ref), state: w.things.kinds.relink, card: () => relinkCard(ref) };
    case "perm": {
      const p = d.perms.find((x) => x.id === ref);
      if (!p) return null;
      const ask = p.kind === "ask_user";
      const q = ask ? p.question?.questions?.[0] : undefined;
      const title = ask ? (q ? q.question : w.things.asksYou(called(p)))
        : p.kind === "write_file" ? w.things.wantsToChange(p.summary) : p.kind === "run_command" ? w.things.wantsToRun(p.summary)
        : p.kind === "open_web" ? w.things.wantsToOpen(p.domain || p.url) : w.things.wantsToDo(p.summary);
      return { id: wt.id, kind: "perm", title, state: `${called(p)} · ${w.things.kinds.perm}`, card: () => permissionCard(p) };
    }
    case "pq": {
      const q = d.asks.find((x) => String(x.id) === ref);
      return q ? { id: wt.id, kind: "project", title: q.text, state: w.things.asksYou(q.project), card: () => projectQuestionCard(q),
                   about: { kind: "project_question", id: q.id } } : null;
    }
    case "talkq": {
      const [first, ...rest] = wt.lines;
      return first ? { id: wt.id, kind: "talkq", title: rest.length ? w.things.firstOfMore(first, rest.length) : first,
                       state: w.things.leftByTalk(wt.by), card: () => talkQuestionsCard(wt) } : null;
    }
    case "c": {
      const c = d.conf.find((x) => String(x.id) === ref);
      if (!c) return null;
      const title = c.kind === "attribution" && c.data && c.data.match ? w.things.whereActivityGoes(c.data.match) : c.question;
      const kind = isKind(c.kind) ? c.kind : "question";
      return { id: wt.id, kind, title, state: w.things.kinds[kind], card: () => c.kind === "attribution" ? confirmationCard(c) : questionCard(c),
               about: { kind: "question", id: c.id } };
    }
    case "r": {
      const r = d.review.find((x) => String(x.id) === ref);
      const sameDomain = r ? d.review.filter((x) => x.domain === r.domain).length : 0;
      return r ? { id: wt.id, kind: "review", title: w.things.reviewTitle(r.title, r.detail), state: `${w.things.kinds.review} · ${r.domain_title}`,
                   card: () => reviewCard([r], sameDomain), about: { kind: "review", id: r.id } } : null;
    }
    case "p": {
      const r = h.ready.find((x) => String(x.id) === ref);
      if (!r) return null;
      const reply = r.kind === "mail_reply";
      return { id: wt.id, kind: reply ? "reply" : "packet", title: r.title,
               state: reply ? w.things.replyDraftFrom(sender(r.from)) : w.things.packetReady(r.prepared_by_label || "Muninn"),
               card: async () => el("div", { class: "packet" }, kids(await renderPacket(String(r.id)))),
               about: reply ? undefined : { kind: "packet", id: r.id } };
    }
    case "t": {
      const t = h.tasks.find((x) => x.key === ref);
      return t ? { id: wt.id, kind: "due", title: t.title, state: `${w.things.kinds.due} · ${dueLabel(t.due, now)}`,
                   card: () => el("section", { class: "card" }, taskRow(t)), about: { kind: "task", id: t.key } } : null;
    }
    case "topic": {
      const tp = d.topics.find((x) => x.thread === ref);
      return tp ? { id: wt.id, kind: "topic", title: tp.title, state: tp.ask || tp.now || (tp.state === "waiting" ? w.things.waitingOnYou : w.things.inProgress),
                    card: () => topicCard(tp) } : null;
    }
    case "lease": {
      const l = d.lent.find((x) => x.thread === ref);
      return l ? { id: wt.id, kind: "lease", title: w.things.deepTalkWith(called(l)),
                   state: l.warn ? w.things.quietAWhile : l.state === "terminal" ? w.things.inTerminal : w.things.going, card: () => leaseCard(l) } : null;
    }
  }
  return null;
}

// a topic of the conversation: where it stands, and the way back to it
function topicCard(tp: TopicState): HTMLElement {
  return el("section", { class: "card", "aria-label": tp.title },
    el("p", { class: "hero-text" }, tp.title),
    tp.ask || tp.now ? el("p", { class: "sub" }, tp.ask || tp.now) : null,
    el("div", { class: "options" }, el("button", { type: "button", class: "suggested",
      onclick: () => openTalk(tp.about, tp.title, null, { thread: tp.thread }) }, w.things.backToThisConversation)));
}

// set aside, and closed today: the topics and ideas no longer open
export function quiet(d: Data): { shelved: number; closed: number } {
  return {
    shelved: d.topics.filter((t) => t.state === "shelved").length + d.home.ideas.filter((i) => i.status === "shelved").length,
    closed: d.topics.filter((t) => t.state === "closed").length + d.home.done.length,
  };
}

// what the pill says: how many wait, and how many things there are today
export function summary(d: Data, list: Thing[]): { waiting: number; today: number } {
  const waiting = list.filter((t) => t.group === "wait").length;
  const q = quiet(d);
  return { waiting, today: list.length + q.shelved + q.closed };
}
