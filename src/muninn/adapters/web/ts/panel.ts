// The one panel beside the conversation (from the right on a wide screen, from below on a phone): today's things,
// one of them in detail, and the four places the rest lives in (today, progress, lists, settings). It only shows;
// to change something the user says so in the conversation.

import { api } from "./api.js";
import { el, kids, type Child } from "./dom.js";
import { fmtMD, fmtTime } from "./format.js";
import { renderMail, renderPacket, renderReflection, renderSettings } from "./pages.js";
import { loadToday } from "./shell.js";
import { openTalk, setTopic } from "./talk.js";
import { daysUntil, dueLabel, quiet, type Data, type Thing } from "./things.js";
import type { Day, Goal, Home } from "./types.js";
import { w } from "./words.js";

export type View = "things" | "item" | "packet" | "mail" | "reflection" | "today" | "progress" | "lists" | "settings";
const VIEWS: readonly View[] = ["things", "item", "packet", "mail", "reflection", "today", "progress", "lists", "settings"];
// addresses that cards, pushes and older links use, and where they lead now
const ALIAS: Record<string, View | ""> = {
  home: "", inbox: "", me: "settings", tasks: "lists", ideas: "lists", tracker: "progress", places: "today", reviews: "today",
};

export interface Route { view: View | ""; param: string }

export function route(): Route {
  const [r = "", param = ""] = location.hash.slice(1).split("/");
  const view = (VIEWS as readonly string[]).includes(r) ? (r as View) : ALIAS[r] ?? "";
  return { view, param: decodeURIComponent(param) };
}

// what each view last drew, by its address: shown again at once next time, while it is read again
const drawn = new Map<string, Child[]>();
let drawing = 0;  // a slower, older draw must not overwrite a newer one

// The panel at once: its head and what is in memory (today's things need nothing more; a view that reads its own
// data shows what it drew last time, or a skeleton), then that view's own data when it comes. `data` is null
// before the first read of what waits. `fresh`: something changed, so a view that reads its own reads again.
export function renderPanel(panel: HTMLElement, r: Route, data: Data | null, list: Thing[], fresh = false): void {
  if (!r.view) return;
  const view = r.view;
  const mine = ++drawing;
  const key = `${view}/${r.param}`;
  const top = view === "things";
  const body = el("div", { class: "panel-body" });
  const typed = panel.dataset.key === key ? drafts(panel) : new Map<string, string>();  // the same view drawn again
  const fill = (children: Child[]): void => {
    const now = new Map([...typed, ...drafts(body)]);  // typed meanwhile into what this draw showed first
    body.replaceChildren(...kids(children));
    keep(body, now);
  };
  if (panel.dataset.key !== key) setTopic(null, null);  // a view about one thing names it while it draws
  panel.dataset.key = key;
  panel.replaceChildren(...kids([
    el("div", { class: "panel-head" },
      top ? el("b", { class: "panel-title" }, data ? w.panel.todayCount(countToday(data, list)) : w.panel.titles.things)
        : el("a", { class: "back", href: "#things", "aria-label": w.panel.backLabel }, w.panel.back),
      el("a", { class: "close", href: "#", "aria-label": w.panel.collapse }, w.panel.collapse)),
    w.panel.titles[view] && !top ? el("h2", { class: "panel-view" }, w.panel.titles[view]) : null,
    body]));
  if (!data) { fill([skeleton()]); return; }
  const got = VIEWS_DRAW[view](data, list, r.param);
  if (!(got instanceof Promise)) { fill(got); drawn.set(key, got); return; }
  const before = drawn.get(key);
  fill(before || [skeleton()]);
  if (before && !fresh && view === "item") return;  // one thing's card: what it showed stays until something changes
  got.then((children) => {
    drawn.set(key, children);
    if (mine === drawing) fill(children);
  }).catch(() => { if (mine === drawing && !before) fill([el("p", { class: "empty" }, w.panel.cannotRead)]); });
}

// what the user typed into the view's boxes and has not sent, by box: a view drawn again (something changed, or the
// app came back into view) puts it back, so nothing typed is lost to a redraw
const TYPED = "textarea, input:not([type]), input[type=text], input[type=search], input[type=email], input[type=url], input[type=number]";

function boxes(root: ParentNode): [string, HTMLInputElement | HTMLTextAreaElement][] {
  const seen = new Map<string, number>();
  return Array.from(root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(TYPED)).map((b) => {
    const name = `${b.closest<HTMLElement>("[data-thing]")?.dataset.thing || ""}|${b.name || b.getAttribute("aria-label") || b.placeholder || ""}`;
    const n = seen.get(name) || 0;
    seen.set(name, n + 1);
    return [`${name}|${n}`, b];
  });
}

function drafts(root: ParentNode): Map<string, string> {
  return new Map(boxes(root).filter(([, b]) => b.value !== b.defaultValue).map(([k, b]) => [k, b.value]));
}

function keep(root: ParentNode, typed: Map<string, string>): void {
  if (!typed.size) return;
  for (const [k, b] of boxes(root)) {
    const v = typed.get(k);
    if (v !== undefined && b.value === b.defaultValue) b.value = v;
  }
}

// something about to be filled in: a few grey lines (still, for those who asked for less motion)
const skeleton = (): HTMLElement => el("div", { class: "skeleton", "aria-busy": "true", "aria-label": w.panel.loading },
  el("span", {}), el("span", {}), el("span", { class: "short" }));

const countToday = (d: Data, list: Thing[]): number => { const q = quiet(d); return list.length + q.shelved + q.closed; };

type Draw = (d: Data, list: Thing[], param: string) => Promise<Child[]> | Child[];
const VIEWS_DRAW: Record<View, Draw> = {
  things: drawThings,
  item: drawItem,
  packet: (_d, _l, id) => renderPacket(id).then((c) => [el("div", { class: "box packet" }, kids(c))]),
  mail: (_d, _l, key) => renderMail(key).then((c) => [el("div", { class: "box" }, kids(c))]),
  reflection: (_d, _l, id) => renderReflection(id).then((c) => [el("div", { class: "box" }, kids(c))]),
  today: drawToday,
  progress: drawProgress,
  lists: drawLists,
  settings: async () => [el("div", { class: "settings" }, kids(await renderSettings({ questions: false })))],
};

// ── today's things ───────────────────────────────────────────────────────

function group(label: string, tone: string, rows: Child[]): HTMLElement | null {
  if (!rows.length) return null;
  return el("section", { class: `group ${tone}`, "aria-label": label },
    el("div", { class: "group-head" }, el("span", { class: "group-name" }, label), el("span", { class: "count" }, String(rows.length))),
    el("div", { class: "rows" }, rows));
}

const thingRow = (t: Thing): HTMLElement =>
  el("a", { class: "row two", href: `#item/${encodeURIComponent(t.id)}` }, el("span", { class: "name" }, t.title), el("span", { class: "state" }, t.state));

function drawThings(d: Data, list: Thing[]): Child[] {
  const h = d.home;
  const q = quiet(d);
  const goals = (h.week || []).length;
  const behind = (h.week || []).filter((x) => x.state === "behind").length;
  return [
    list.length ? null : el("p", { class: "empty" }, w.panel.nothingWaits),
    group(w.panel.waiting, "wait", list.filter((t) => t.group === "wait").map(thingRow)),
    group(w.panel.doing, "doing", list.filter((t) => t.group === "doing").map(thingRow)),
    q.shelved || q.closed ? el("a", { class: "row quiet", href: "#lists" }, w.panel.quietRow(q.shelved, q.closed)) : null,
    el("nav", { class: "rows more", "aria-label": w.panel.seeMore },
      place("today", w.panel.titles.today, h.schedule.length ? w.panel.scheduledLeft(h.schedule.length) : h.places.length ? w.panel.placesVisited(h.places.length) : ""),
      place("progress", w.panel.titles.progress, goals ? w.panel.goalsHint(goals, behind) : ""),
      place("lists", w.panel.titles.lists, w.panel.listsHint(h.tasks.length, h.ideas.filter((i) => i.status === "open").length)),
      place("settings", w.panel.titles.settings, "")),
  ];
}

const place = (view: View, name: string, hint: string): HTMLElement =>
  el("a", { class: "row", href: `#${view}` }, el("span", { class: "name" }, name), hint ? el("span", { class: "hint" }, hint) : null);

async function drawItem(_d: Data, list: Thing[], id: string): Promise<Child[]> {
  const t = list.find((x) => x.id === id);
  if (!t) {  // done (here or elsewhere): its page has nothing left, so back to what still waits
    if (route().view === "item") setTimeout(() => { if (route().view === "item" && route().param === id) location.hash = "#things"; }, 0);
    return [el("p", { class: "empty" }, w.panel.handled)];
  }
  if (t.about) setTopic(t.about, t.title);  // what is said while it is open is about it
  return [
    el("div", { class: "box item", "data-thing": t.id }, await t.card()),  // the card says what it is and where it stands
    t.about ? el("button", { type: "button", class: "link-btn", onclick: () => t.about && openTalk(t.about, t.title) }, w.panel.talkAboutThis) : null,
  ];
}

// ── today ─────────────────────────────────────────────────────────────────

async function drawToday(d: Data): Promise<Child[]> {
  const day = await api<Day>("/api/day");
  const h: Home = d.home;
  const now = new Date(day.now);
  const worked = day.hours.reduce((k, x) => k + x.hours, 0);
  const ahead = day.events.filter((e) => new Date(e.start) > now).length;
  const here = day.stays.length ? day.stays[day.stays.length - 1]?.place || w.panel.unnamedPlace : "—";
  type Line = { start: string; end: string; kind: "did" | "event" | "planned"; text: string };
  const lines: Line[] = [
    ...day.activity.map((a) => ({ start: a.start, end: a.end, kind: "did" as const, text: a.title })),
    ...day.events.map((e) => ({ start: e.start, end: e.end, kind: e.own ? "planned" as const : "event" as const,
                               text: e.own ? w.panel.scheduledByMuninn(e.title.replace(/^Muninn · /, "")) : e.title })),
  ].sort((a, b) => a.start.localeCompare(b.start));
  const rows: Child[] = [];
  let nowShown = false;
  for (const x of lines) {
    if (!nowShown && new Date(x.start) > now) {
      rows.push(el("span", { class: "time now" }, fmtTime(day.now)), el("div", { class: "now-line" }, el("span", {}, w.panel.now)));
      nowShown = true;
    }
    rows.push(el("span", { class: "time" }, `${fmtTime(x.start)}–${fmtTime(x.end)}`), el("div", { class: `slot ${x.kind}` }, x.text));
  }
  if (!nowShown) rows.push(el("span", { class: "time now" }, fmtTime(day.now)), el("div", { class: "now-line" }, el("span", {}, w.panel.now)));
  return [
    el("div", { class: "stats" },
      stat(w.panel.worked, w.panel.hours(worked)), stat(w.panel.eventsLeft, w.panel.eventsCount(ahead)), stat(w.panel.where, here)),
    el("section", { class: "box timeline", "aria-label": w.panel.timeline },
      el("div", { class: "legend" }, el("span", { class: "k did" }, w.panel.legendDid), el("span", { class: "k event" }, w.panel.legendEvent), el("span", { class: "k planned" }, w.panel.legendPlanned)),
      el("div", { class: "lines" }, rows)),
    day.stays.length ? el("section", { class: "box rows", "aria-label": w.panel.where }, day.stays.map((s) =>
      el("div", { class: "row" }, el("span", { class: "name" }, s.place || w.panel.unnamedPlace), el("span", { class: "hint" }, `${fmtTime(s.start)}–${fmtTime(s.end)}`)))) : null,
    h.reflection ? el("a", { class: "row quiet", href: `#reflection/${h.reflection.id}` }, w.panel.weekReflection(h.reflection.title)) : null,
  ];
}

const stat = (label: string, value: string): HTMLElement =>
  el("div", { class: "stat" }, el("span", { class: "label" }, label), el("b", {}, value));

// ── progress ──────────────────────────────────────────────────────────────

function goalBar(g: Goal): HTMLElement {
  const ratio = g.actual === null ? 0 : g.direction === "at_most" ? (g.actual > g.target ? 1 : 0) : Math.min(1, g.actual / (g.target || 1));
  const pace = g.direction === "at_least" && g.expected < g.target && g.state !== "done" && g.state !== "unknown"
    ? Math.min(1, g.expected / (g.target || 1)) : null;
  const behind = g.state === "behind";
  return el("div", { class: `goal ${behind ? "behind" : ""}` },
    el("div", { class: "goal-line" }, el("span", {}, g.title),
      el("span", { class: "value" }, g.value, g.pending ? el("span", { class: "chip" }, w.panel.toConfirm) : behind ? el("span", { class: "chip behind" }, g.state_label) : null)),
    el("div", { class: "bar" }, el("div", { class: "fill", style: `width:${Math.round(ratio * 100)}%` }),
      pace !== null ? el("div", { class: "pace", style: `left:${Math.round(pace * 100)}%` }) : null));
}

async function drawProgress(): Promise<Child[]> {
  const t = await loadToday();
  const now = new Date(t.now);
  const out: Child[] = [];
  for (const d of t.domains) {
    if (!d.goals.length && !d.tracker.length && !d.deadlines.length) continue;
    const open = d.tracker.filter((r) => !["rejected", "withdrawn"].includes(r.status));
    out.push(el("section", { class: "box domain", "aria-label": d.title },
      el("b", { class: "domain-title" }, d.title),
      d.goals.map(goalBar),
      d.deadlines.map((x) => el("div", { class: "row" }, el("span", { class: "name" }, w.panel.deadline(x.title)),
        el("span", { class: "hint" }, `${fmtMD(x.due)} · ${daysUntil(x.due, now) >= 0 ? w.panel.daysLeft(daysUntil(x.due, now)) : w.panel.daysPast(-daysUntil(x.due, now))}`))),
      open.length ? el("div", { class: "tracker" },
        el("span", { class: "label" }, w.panel.applications),
        open.slice(0, 3).map((r) => el("div", { class: "row" }, el("span", { class: "name" }, r.title),
          el("span", { class: "hint" }, t.status_labels[r.status] || r.status))),
        open.length > 3 ? el("details", {}, el("summary", {}, w.panel.moreCompanies(open.length - 3)),
          open.slice(3).map((r) => el("div", { class: "row" }, el("span", { class: "name" }, r.title),
            el("span", { class: "hint" }, t.status_labels[r.status] || r.status)))) : null) : null));
  }
  if (!out.length) out.push(el("p", { class: "empty" }, w.panel.noGoals));
  out.push(el("p", { class: "note" }, w.panel.paceNote));
  return out;
}

// ── lists ─────────────────────────────────────────────────────────────────

function talkRow(about: { kind: "task" | "idea"; id: string | number }, name: string, hint: string): HTMLElement {
  return el("button", { type: "button", class: "row", "aria-label": name, onclick: () => openTalk(about, name) },
    el("span", { class: "name" }, name), hint ? el("span", { class: "hint" }, hint) : null);
}

// a topic set aside or closed: pressing it goes back to that conversation
function topicRow(t: Data["topics"][number]): HTMLElement {
  return el("button", { type: "button", class: "row", "aria-label": t.title, onclick: () => openTalk(t.about, t.title, null, { thread: t.thread }) },
    el("span", { class: "name" }, t.title), t.now ? el("span", { class: "hint" }, t.now) : null);
}

function drawLists(d: Data): Child[] {
  const h = d.home;
  const now = new Date(h.now);
  const open = h.ideas.filter((i) => i.status === "open"), shelved = h.ideas.filter((i) => i.status === "shelved");
  const listOf = (label: string, rows: Child[]) => rows.length ? el("section", { class: "group plain", "aria-label": label },
    el("div", { class: "group-head" }, el("span", { class: "group-name" }, label), el("span", { class: "count" }, String(rows.length))),
    el("div", { class: "rows" }, rows)) : null;
  return [
    el("p", { class: "note" }, w.panel.listsNote),
    listOf(w.panel.tasks, h.tasks.map((t) => talkRow({ kind: "task", id: t.key }, t.title,
      [t.due ? dueLabel(t.due, now) : "", w.panel.joinList(t.projects)].filter(Boolean).join(" · ")))),
    listOf(w.panel.ideas, open.map((i) => talkRow({ kind: "idea", id: i.n }, i.text, i.talk ? w.panel.talkedAbout : ""))),
    listOf(w.panel.shelved, [
      ...d.topics.filter((t) => t.state === "shelved").map((t) => topicRow(t)),
      ...shelved.map((i) => talkRow({ kind: "idea", id: i.n }, i.text, w.panel.shelved))]),
    listOf(w.panel.closedTopics, d.topics.filter((t) => t.state === "closed").map((t) => topicRow(t))),
    h.my_project_todos.length ? el("details", { class: "box" }, el("summary", {}, w.panel.projectTodos(h.my_project_todos.reduce((k, p) => k + p.items.length, 0))),
      h.my_project_todos.map((p) => el("div", {}, el("b", { class: "mono" }, p.project), el("ul", {}, p.items.map((x) => el("li", {}, x)))))) : null,
    h.done.length ? el("details", { class: "box" }, el("summary", {}, w.panel.doneToday(h.done.length)),
      h.done.map((x) => el("div", { class: "row" }, el("span", { class: "name" }, x.text), el("span", { class: "hint" }, fmtTime(x.ts))))) : null,
  ];
}
