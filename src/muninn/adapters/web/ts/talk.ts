// The conversation with Muninn: one stream on the home, made of every conversation kept (each about an item or
// about anything), with a divider where the topic changes. What is said goes to the conversation on now; a
// conversation can be lent to a project's session (a deep talk), whose turns show in the same stream.

import { api } from "./api.js";
import { called } from "./format.js";
import { at, days, foldEarlier } from "./stamps.js";
import { hasFiles, mountFiles, sentNode, takeFiles } from "./files.js";
import { el, kids, must, toast, type Child } from "./dom.js";
import { cloudName, handleError, render } from "./shell.js";
import { w } from "./words.js";
import type { About, CommandReply, Handover, LeaseInfo, LeaseMode, LeaseView, Lent, List, PermissionRequest, Sent, StepKind, Talk, Thread, Turn } from "./types.js";

interface Conversation {
  thread: string | null;
  pastLease: string | null;
  about: About | null;
  log: HTMLElement;
  input: HTMLTextAreaElement;
  lease: (LeaseView | { lease_id: string; project: string; label?: string; state: string; mode?: LeaseMode; can_work?: boolean }) | null;
  stream: EventSource | null;
  pinned: boolean;  // the user opened this conversation just now: what they say next is about it, not judged
  bubble: HTMLElement | null;
  status: HTMLElement | null;
  steps?: { box: HTMLDetailsElement; head: HTMLElement; list: HTMLElement; n: number } | null;  // what the session is doing this turn
  stuck?: boolean;  // the session failed: taking it back by hand is offered
  resuming?: string | null;  // the lease that goes on once its handover is written (what was said waits for it)
  quotaNote?: string;
}

let talk: Conversation | null = null;  // the stream, once mounted
const known = new Map<string, About | null>();  // every conversation kept: what each is about
const shownLeases = new Set<string>();  // deep talks whose turns are already in the stream
const shownSpoken = new Set<string>();  // what Muninn brought up unasked, already in the stream (thread + ts)

// ── the topic: what the user is looking at, so what they say is about it ──

export interface Topic { about: About | null; title: string | null }
let topic: Topic = { about: null, title: null };

export const sameAbout = (a: About | null | undefined, b: About | null | undefined): boolean =>
  !!(a && b && a.kind === b.kind && String(a.id) === String(b.id));

// a view declares what it shows; what the user says next is about that
export function setTopic(about: About | null, title: string | null): void {
  topic = { about, title };
}
export const currentTopic = (): Topic => topic;

// the stream is opened from somewhere that may cover it (the panel on a phone): it is told to make way
let onOpen: () => void = () => {};
export function watchOpen(fn: () => void): void {
  onOpen = fn;
}

// what the input does with what was typed: go on with the conversation on now, or the one about what is on screen
export function tell(text: string): void {
  const t = talk;
  if (!t) return;
  if (topic.about && !sameAbout(t.about, topic.about)) openTalk(topic.about, topic.title, text);
  else say(text);
}

// a conversation to resume: its thread, and the deep talk it came from if that one has ended
export interface Resume { thread: string; last?: string; lease_id?: string | null }

export function mountTalk(): void {
  const input = must<HTMLTextAreaElement>("#say-input");
  talk = { thread: null, pastLease: null, about: null, log: must("#log"), input, lease: null, stream: null, pinned: false, bubble: null, status: null };
  mountFiles();
  must("#say").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text && !hasFiles()) return;
    input.value = "";
    fit(input);
    tell(text);
  });
  // Enter says it, Shift+Enter starts a new line; Enter that picks a word in an input method (Zhuyin, Pinyin) only picks it
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    must<HTMLFormElement>("#say").requestSubmit();
  });
  input.addEventListener("input", () => fit(input));
}

// the box as tall as what is in it (up to the few lines the page allows, then it scrolls)
export function fit(box: HTMLTextAreaElement): void {
  box.style.height = "auto";
  box.style.height = `${box.scrollHeight}px`;
}

// a sentence is on its way (the input waits for its answer): nothing redraws the stream meanwhile
export const talking = (): boolean => !!talk && talk.input.disabled;

// the conversation as last read from the server: when it is not what the server has now, something was said
// elsewhere (on another device) while this one was away
let synced = "";
let stale = false;  // something changed elsewhere while the user was in the middle of something: drawn next time
// a change came while the user was in the middle of something here: not drawn under them, but next time
export const markStale = (): void => { stale = true; };
const shape = (feed: List<Thread>): string => feed.items.map((th) => `${th.thread}:${th.turns.length}:${th.lease_id || ""}`).join("|");

// every conversation kept, oldest first, as one stream; what is said next goes on with the latest
export async function loadStream(): Promise<void> {
  if (!talk) return;
  draw(await api<List<Thread>>("/api/conversation"));
}

async function draw(feed: List<Thread>): Promise<void> {
  const t = talk;
  if (!t) return;
  synced = shape(feed);
  stale = false;
  known.clear();
  const talks = await talkLines(feed);
  const nodes: Child[] = [];
  for (const th of feed.items) {
    known.set(th.thread, th.about);
    for (const x of th.turns) if (x.spoken) shownSpoken.add(`${th.thread} ${x.ts}`);
    if (!th.turns.length) continue;
    if (th.title) nodes.push(divider(w.talk.talkingAbout(th.title), th.thread));  // a divider only where there is a name to put on it
    for (const x of th.turns) {
      const stretch = x.ended ? talks.get(`${x.ended} ${x.ts}`) : undefined;
      if (stretch && stretch.turns.length) nodes.push(talkFold(stretch.label, stretch.turns));  // what was said in it, folded
      nodes.push(at(x.role === "user" ? saidLine(x) : x.handover ? handoverCard(x.handover) : x.note ? receipt(x.text) : el("p", { class: "reply" }, x.text), x.ts));
      if (x.spoken) nodes.push(suggestRow(th.suggest.filter((s) => s.turn === x.ts), th.thread, x.ts || undefined));  // its own buttons, under it
    }
    nodes.push(suggestRow(unplaced(th), th.thread));
  }
  t.log.replaceChildren(...kids(nodes));
  foldEarlier(t.log);  // the conversation opens on today
  if (t.pinned) {  // the user just chose what to talk about and has not said it yet: a redraw keeps that, and says so
    const chosen = topic.title && !(t.thread && t.thread === feed.items[feed.items.length - 1]?.thread);
    if (chosen) t.log.append(divider(w.talk.nowTalkingAbout(topic.title!)));
    scrollEnd(t);
    return;
  }
  const last = feed.items[feed.items.length - 1];
  if (t.stream && (!last || !last.lease_id)) { t.stream.close(); t.stream = null; t.lease = null; renderLeaseBar(t); }
  if (last) {
    t.thread = last.thread;
    t.about = last.about;
    t.pinned = false;
    if (last.lease_id) await resumeLease(t);
  }
  scrollEnd(t);
}

// what was said in a deep talk that has ended: one line saying with whom and how much, the talk itself folded under it
// (where it began is the line above, kept in the conversation; its handover card, right after, is where it ended)
function talkFold(label: string, turns: Turn[]): HTMLElement {
  const said = turns.filter((t) => t.role === "user" || t.role === "session").length;
  return at(el("details", { class: "talk-fold" },
    el("summary", {}, w.talk.talkFold(label, said)),
    el("div", { class: "talk-lines" }, turns.map((x) => turnLine(x, label)))), turns[0]?.ts);
}

// when each deep talk last ended, as the conversation on screen shows it: a talk taken up again goes on from there
const talkEnds = new Map<string, string>();

// what was said in each deep talk that has ended, by the turn that closed it ("<lease> <ts>"): a talk taken up again
// is the same lease, so each closing turn gets the lines since the one before it
async function talkLines(feed: List<Thread>): Promise<Map<string, { label: string; turns: Turn[] }>> {
  const ends = new Map<string, string[]>();  // lease → its closing turns' times, in order
  for (const th of feed.items) for (const x of th.turns) if (x.ended && x.ts) ends.set(x.ended, [...(ends.get(x.ended) || []), x.ts]);
  const out = new Map<string, { label: string; turns: Turn[] }>();
  talkEnds.clear();
  for (const [lease, times] of ends) talkEnds.set(lease, times[times.length - 1] || "");
  await Promise.all([...ends].map(async ([lease, times]) => {
    let turns: Turn[] = [];
    let label = w.talk.project;
    try {
      const x = (await api<LeaseInfo>(`/api/leases?lease=${encodeURIComponent(lease)}`)).transcript;
      turns = x?.turns || [];
      label = x ? called(x) : label;
    } catch { return; }  // the conversation is drawn without them rather than not at all
    let since = "";
    for (const ts of times) {
      out.set(`${lease} ${ts}`, { label, turns: turns.filter((t) => (t.ts || "") > since && (t.ts || "") <= ts) });
      since = ts;
    }
  }));
  return out;
}

// what Muninn brought up while the stream was open (when the user paused): said here, and what the user says next
// answers it. `away`: the app is back in view after being away; whatever was said meanwhile on another device
// (or anything else the server has that this stream lacks) is drawn as a fresh open would draw it.
export async function refreshStream(away = false): Promise<void> {
  const t = talk;
  if (!t) return;
  const feed = await api<List<Thread>>("/api/conversation");
  if (away && shape(feed) !== synced) {
    await draw(feed);
    return;
  }
  if (!stale) synced = shape(feed);  // stale: what changed elsewhere is still to be drawn
  for (const th of feed.items) {
    known.set(th.thread, th.about);
    const fresh = th.turns.filter((x) => x.spoken && !shownSpoken.has(`${th.thread} ${x.ts}`));
    if (!fresh.length) {
      // buttons on the screen the server no longer offers (what they answer was answered elsewhere: in the panel,
      // on another device) go, though nothing was said
      if (stalePresses(th)) placeButtons(th.thread, th.suggest);
      continue;
    }
    if (th.title) t.log.append(divider(w.talk.talkingAbout(th.title), th.thread));
    for (const x of fresh) {
      shownSpoken.add(`${th.thread} ${x.ts}`);
      t.log.append(at(el("p", { class: "reply" }, x.text), x.ts));
    }
    placeButtons(th.thread, th.suggest);  // each under its line; a line said again has them only under the latest
    scrollEnd(t);
    // a deep talk open here stays open: what Muninn brought up is answered with its buttons, and what is said next
    // still goes to the talk
    if (t.stream && th.thread !== t.thread) continue;
    t.thread = th.thread;
    t.about = th.about;
    t.pinned = true;  // what they say next answers it
    scrollEnd(t);
  }
}

// what came of what the user just did (an answer to a request, a switch, where their files went): one small line under it,
// its details folded away until asked for
function receipt(text: string, detail?: string | null): HTMLElement {
  return detail
    ? el("details", { class: "receipt" }, el("summary", {}, text), folded(detail))
    : el("div", { class: "receipt" }, text);
}

// a note's details, one per line; paths in one folder say the folder once
function folded(detail: string): HTMLElement {
  const lines = detail.split("\n").filter(Boolean);
  const first = lines[0] || "";
  const dir = lines.every((l) => l.includes("/")) ? first.slice(0, first.lastIndexOf("/") + 1) : "";
  const same = dir && lines.every((l) => l.startsWith(dir) && !l.slice(dir.length).includes("/"));
  return el("div", { class: "detail" }, same ? el("div", { class: "dir" }, w.talk.inFolder(dir)) : null,
    el("ul", {}, lines.map((l) => el("li", {}, same ? l.slice(dir.length) : l))));
}

// what the user said, with the files they sent with it
const saidLine = (x: { text: string; files?: Sent[] }): HTMLElement =>
  el("div", { class: "said" }, x.text || null, x.files && x.files.length ? el("div", { class: "sent-files" }, x.files.map((s) => sentNode(s))) : null);

function divider(text: string, thread?: string | null): HTMLElement {
  return el("div", { class: "divider", role: "separator", ...(thread ? { "data-thread": thread } : {}) }, el("span", {}, text));
}

// talk about `about` (or about nothing in particular) from now on: the conversation already kept about it, else a new one
export function openTalk(about: About | null, title: string | null, first?: string | null, resume?: Resume | Talk): void {
  const t = talk;
  if (!t) return;
  onOpen();
  const thread = resume ? resume.thread : about ? [...known].find(([, a]) => sameAbout(a, about))?.[0] ?? null : null;
  if (thread !== t.thread || !sameAbout(about, t.about) || !about) {
    if (t.stream) t.stream.close();
    t.stream = null;
    t.lease = null;
    renderLeaseBar(t);
    t.thread = thread;
    t.about = about;
    t.pastLease = resume && "lease_id" in resume ? resume.lease_id ?? null : null;
    // moved on before saying anything there: that divider goes, so looking through things never stacks them up
    if (t.log.lastElementChild?.classList.contains("divider")) t.log.lastElementChild.remove();
    if (title) t.log.append(divider(w.talk.nowTalkingAbout(title)));
    if (t.thread) resumeLease(t);
  }
  setTopic(about, title);
  t.pinned = true;
  if (first) say(first);
  else t.input.focus();
  scrollEnd(t);
}

// the deep talk's stream is dropped (the screen is going away)
export function closeTalk(): void {
  if (talk && talk.stream) talk.stream.close();
  if (talk) talk.stream = null;
}

// Muninn judged the sentence belongs to another conversation (or a new one): a divider says so, above it
function switchedTo(t: Conversation, thread: string | null, title: string | null, said: HTMLElement): void {
  if (t.stream && thread !== t.thread) { t.stream.close(); t.stream = null; t.lease = null; renderLeaseBar(t); }
  t.about = thread ? known.get(thread) ?? null : null;
  if (title) said.before(divider(w.talk.switchedTo(title)));
  if (thread && thread !== t.thread) {
    t.thread = thread;
    resumeLease(t);
  }
}

export async function say(text: string): Promise<void> {
  const t = talk;
  if (!t) return;
  const files = await takeFiles();  // what waits under the input goes with it
  const said = at(el("div", { class: "said" }, text || null, files.sent.length ? el("div", { class: "sent-files" }, files.sent.map((s, i) => sentNode(s, files.previews[i]))) : null));
  t.log.append(said);
  for (const why of files.failed) t.log.append(el("p", { class: "muted" }, why));
  if (!text && !files.sent.length) return;
  const thinking = el("div", { class: "thinking" }, t.lease ? w.talk.handingTo(called(t.lease)) : w.talk.thinking);
  t.log.append(thinking);
  scrollEnd(t);
  t.input.disabled = true;
  try {
    const pinned = t.pinned;
    t.pinned = false;
    const r = await api<CommandReply>("/api/command", { text, thread: t.thread, about: pinned ? t.about : null, pinned,
                                                        attachments: files.sent.map((s) => s.id), client_msg_id: Math.random().toString(36).slice(2) });
    if (r.topic && r.topic.switched) switchedTo(t, r.thread || null, r.topic.title, said);
    if (r.thread) {
      t.thread = r.thread;
      known.set(r.thread, t.about);
    }
    if (leaseReply(t, r, thinking)) return;
    const nodes = turnNodes(r);
    thinking.replaceWith(...nodes);
    placeButtons(t.thread, r.suggest, nodes[nodes.length - 1]);
    if (r.lent) lent(t, r.lent);
    render();  // what was said may have answered or changed what is waiting
  } catch (err) {
    thinking.replaceWith(el("p", { class: "reply" }, w.talk.notSent));
    if (text && !t.input.value) { t.input.value = text; fit(t.input); }  // back where it was typed, to send again
    handleError(err);
  } finally {
    t.input.disabled = false;
    scrollEnd(t);
    t.input.focus();
  }
}

const scrollEnd = (t: Conversation): void => {
  days(t.log);  // whatever was just added, the day it was said on is named above it
  if (t.log.lastElementChild) t.log.lastElementChild.scrollIntoView({ block: "end" });
};

// ── lending the conversation to a project's session ─────────────────────

// what /api/command answered when the thread is (or becomes) lent; false when it is an ordinary turn
function leaseReply(t: Conversation, r: CommandReply, thinking: HTMLElement): boolean {
  if (r.needs_confirm) {
    const project = r.project || "";
    thinking.replaceWith(el("div", { class: "outcome" }, el("span", { class: "text" }, r.needs_confirm),
      el("span", { class: "acts" }, el("button", { type: "button", class: "btn small", onclick: () => attach(project, true) }, w.talk.startAnyway))));
    return true;
  }
  if (r.note && r.mode) {  // switched with /work or /discuss
    thinking.remove();
    modeSwitched(t, r.mode, r.note);
    return true;
  }
  if (r.lease_id && r.project && !r.leased) {  // just attached
    thinking.remove();
    startLease(t, { lease_id: r.lease_id, project: r.project, label: r.label, state: r.state || "attaching", mode: r.mode, can_work: r.can_work });
    return true;
  }
  if (r.leased) {
    thinking.remove();
    if (r.lent) lent(t, r.lent);  // given back a moment ago, and lent again as they came back to it
    t.resuming = null;
    t.bubble = null;
    setStatus(t, w.talk.isThinking(called(t.lease)));
    return true;
  }
  if (r.state === "detaching") {
    thinking.replaceWith(el("p", { class: "thinking" }, w.talk.takingBack));
    return true;
  }
  return false;
}

// trying an idea out: the conversation goes on with its sandbox, which starts from what was said here
function lent(t: Conversation, x: Lent): void {
  if (x.error) { t.log.append(el("p", { class: "thinking" }, x.error)); return; }
  if (x.text) t.log.append(at(receipt(x.text)));
  if (x.lease_id && x.project) {
    startLease(t, { lease_id: x.lease_id, project: x.project, label: x.label, state: x.state || "attaching", mode: x.mode, can_work: x.can_work });
    // gone into while it writes its handover: this view stays with it, and it goes on once it has written it
    t.resuming = x.state === "detaching" ? x.lease_id : null;
    if (x.opened) { t.bubble = null; setStatus(t, w.talk.isThinking(called(x))); }
  } else if (x.needs_confirm) {
    const spot = el("p", { class: "thinking" });
    t.log.append(spot);
    leaseReply(t, { needs_confirm: x.needs_confirm, project: x.project }, spot);
  }
}

async function attach(project: string, confirm: boolean): Promise<void> {
  const t = talk;
  if (!t) return;
  try {
    const r = await api<CommandReply>("/api/leases/attach", { thread: t.thread, project, confirm_quota: confirm });
    if (r.thread) t.thread = r.thread;
    const note = el("p", { class: "thinking" });
    t.log.append(note);
    leaseReply(t, r, note);
  } catch (err) { handleError(err); }
}

async function resumeLease(t: Conversation): Promise<void> {
  try {
    const info = await api<LeaseInfo>(`/api/leases?thread=${encodeURIComponent(t.thread || "")}`);
    if (info.enabled && !info.lease && t.pastLease) return await showPastLease(t);  // awaited: its failure is caught here
    if (!info.enabled || !info.lease) return;
    if (!shownLeases.has(info.lease.lease_id)) {  // coming back to it: its turns are already in the stream
      shownLeases.add(info.lease.lease_id);
      const since = talkEnds.get(info.lease.lease_id) || "";  // what came before is above, with how it ended then
      for (const x of info.lease.turns.filter((x) => (x.ts || "") > since)) t.log.append(turnLine(x, called(info.lease)));
    }
    startLease(t, info.lease);
    // what it is still waiting on, answerable right here
    const perms = await api<{ items: PermissionRequest[] }>("/api/permission-requests/pending").catch(() => ({ items: [] as PermissionRequest[] }));
    const id = info.lease.lease_id;
    for (const p of perms.items.filter((p) => p.lease === id)) showAsk(t, p);
  } catch (err) { /* the thread simply is not lent */ }
}

// a deep-talk that already came back: its conversation and handover to reread; what is said now goes to Muninn
async function showPastLease(t: Conversation): Promise<void> {
  const r = await api<LeaseInfo>(`/api/leases?lease=${encodeURIComponent(t.pastLease || "")}`);
  const x = r.transcript;
  if (!x) return;
  t.log.append(...x.turns.map((y) => turnLine(y, called(x))), ...(x.handover ? [at(handoverCard(x.handover), x.ended)] : []),
    el("p", { class: "thinking" }, w.talk.pastTalkBack(called(x))));
  scrollEnd(t);
}

// one line of a deep talk with `label` (what the user calls the project)
function turnLine(x: Turn, label: string): HTMLElement {
  if (x.role === "file") return at(shownFile(x), x.ts);
  if (x.role === "user") return at(saidLine(x), x.ts);
  if (x.role === "answer") return at(receipt(x.text, x.detail), x.ts);
  if (x.role === "muninn") return at(beside(x.text), x.ts);
  if (x.role === "relay") return at(receipt(w.talk.told(label, x.text)), x.ts);
  return at(el("p", { class: "reply session" }, el("span", { class: "body" }, ...markdown(x.text))), x.ts);
}

// a file of the project the session showed him: an image in place (opened whole with a tap), anything else a link;
// its own line under it. Fetched through Muninn, for this conversation only; gone after 30 days, and said so.
function shownFile(x: Turn): HTMLElement {
  const f = (x.files || [])[0];
  if (!f) return el("p", { class: "thinking" }, x.text || "");
  const lease = f.lease || "";
  const url = `/api/leases/${encodeURIComponent(lease)}/files/${encodeURIComponent(f.id)}?thread=${encodeURIComponent(talk?.thread || "")}`;
  const gone = (): HTMLElement => el("p", { class: "muted" }, w.talk.fileExpired(f.name));
  const shown = f.media_type.startsWith("image/")
    ? el("a", { class: "shown-file", href: url, target: "_blank", rel: "noopener" },
      el("img", { src: url, alt: f.name, loading: "lazy", onerror: (ev: Event) => notLoaded(ev.target as HTMLImageElement, url, gone) }))
    : el("a", { class: "shown-file link", href: url, target: "_blank", rel: "noopener" }, w.talk.openFile(f.name));
  return el("figure", { class: "shown" }, shown, x.text ? el("figcaption", {}, x.text) : el("figcaption", { class: "muted" }, f.name));
}

// a picture that did not come (still being written, a line dropped half way): asked again a few times, quietly; it is
// said to be expired only when the server says so, and when it still does not come, a tap tries again
const RETRY_MS = [1000, 3000, 8000];

function notLoaded(img: HTMLImageElement, url: string, gone: () => HTMLElement): void {
  const tries = Number(img.dataset.tries || "0");
  const link = img.closest("a");
  if (!link) return;
  fetch(url, { method: "HEAD", credentials: "same-origin" }).then((r) => r.status).catch(() => 0).then((status) => {
    if (status === 410) { link.replaceWith(gone()); return; }
    if (tries < RETRY_MS.length) {
      img.dataset.tries = String(tries + 1);
      setTimeout(() => { img.src = `${url}&try=${tries + 1}`; }, RETRY_MS[tries]);
      return;
    }
    img.hidden = true;
    const again = el("button", { type: "button", class: "link-btn", onclick: (ev: Event) => {
      ev.preventDefault();
      again.remove();
      img.hidden = false;
      img.dataset.tries = "0";
      img.src = `${url}&try=${Date.now()}`;
    } }, w.talk.notLoaded(img.alt));
    link.after(again);
  });
}

// a session writes markdown: its **bold** and `code` read as such; everything else stays plain text
export function markdown(text: string | null | undefined): Node[] {
  return String(text || "").split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/).filter(Boolean).map((part) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? el("b", {}, part.slice(2, -2))
      : part.startsWith("`") && part.endsWith("`") && part.length > 2 ? el("span", { class: "code" }, part.slice(1, -1))
      : document.createTextNode(part));
}

// whether commands of a kind the user allowed before run without asking (null: not known yet, or never)
let autoCommands: boolean | null = null;

async function readAutoCommands(t: Conversation): Promise<void> {
  try {
    autoCommands = (await api<{ on: boolean | null }>("/api/permissions/auto-commands")).on;
  } catch { autoCommands = null; }
  if (talk === t) renderLeaseBar(t);
}

async function turnAutoCommands(t: Conversation, on: boolean): Promise<void> {
  try {
    autoCommands = (await api<{ on: boolean | null }>("/api/permissions/auto-commands", { on })).on;
    renderLeaseBar(t);
    toast(on ? w.talk.autoOn : w.talk.autoOff);
  } catch (err) { handleError(err); }
}

function startLease(t: Conversation, lease: Conversation["lease"] & object): void {
  t.lease = lease;
  if (autoCommands === null) void readAutoCommands(t);
  t.stuck = !!("error" in lease && lease.error);  // it failed before this view opened
  shownLeases.add(lease.lease_id);
  renderLeaseBar(t);
  if (lease.can_work && lease.mode !== "work") workOffer(t);
  t.input.placeholder = w.talk.talkTo(called(lease));
  if (t.stream) t.stream.close();
  const es = new EventSource(`/api/leases/${encodeURIComponent(lease.lease_id)}/events`);
  t.stream = es;
  es.onmessage = (m) => { if (talk === t) leaseEvent(t, JSON.parse(m.data) as LeaseEvent); };
}

// a lent conversation: a line over the input names the project, with the rarer actions
function renderLeaseBar(t: Conversation): void {
  const bar = must("#lease-bar");
  const l = t.lease;
  bar.hidden = !l;
  if (!l) { bar.replaceChildren(); t.input.placeholder = w.talk.talkToMuninn; return; }
  t.input.placeholder = w.talk.talkTo(called(l));
  bar.replaceChildren(
    el("span", { class: "who" }, w.talk.leaseWho(called(l), l.mode !== "work" ? w.talk.readOnly : autoCommands ? w.talk.handsOnAuto : w.talk.handsOnAsk)),
    el("span", { class: "acts" },
      l.mode === "work" && autoCommands !== null ? el("button", { type: "button", class: "link-btn", onclick: () => turnAutoCommands(t, !autoCommands) },
        autoCommands ? w.talk.askEvery : w.talk.runLearned) : null,
      l.can_work ? el("button", { type: "button", class: "link-btn", onclick: () => setMode(t, l.mode === "work" ? "discuss" : "work") },
        l.mode === "work" ? w.talk.backToReadOnly : w.talk.allowHandsOn) : null,
      el("button", { type: "button", class: "link-btn", onclick: () => toTerminal(t) }, w.talk.toTerminal),
      // it comes back by itself; taking it back by hand is only for a talk that is stuck or out in a terminal
      t.stuck || l.state === "terminal" ? el("button", { type: "button", class: "link-btn", onclick: () => detach(t) }, w.talk.takeBack) : null));
}

// a sandbox lent read-only (a lease from before sandboxes started able to act): the user can let it act
function workOffer(t: Conversation): void {
  if (t.log.querySelector(".work-offer")) return;
  const box = el("div", { class: "outcome work-offer" },
    el("span", { class: "text" }, w.talk.workOffer),
    el("span", { class: "acts" }, el("button", { type: "button", class: "btn small", onclick: () => { box.remove(); setMode(t, "work"); } }, w.talk.allowHandsOn)));
  t.log.append(box);
}

async function setMode(t: Conversation, mode: LeaseMode): Promise<void> {
  try {
    const r = await api<{ mode: LeaseMode; note?: string }>(`/api/leases/${mode}`, { thread: t.thread });
    modeSwitched(t, r.mode, r.note);
  } catch (err) { handleError(err); }
}

function modeSwitched(t: Conversation, mode: LeaseMode, note?: string): void {
  if (t.lease) t.lease.mode = mode;
  t.log.querySelector(".work-offer")?.remove();
  renderLeaseBar(t);
  if (note) t.log.append(at(receipt(note)));
  scrollEnd(t);
}

function sessionBubble(t: Conversation): HTMLElement {
  if (!t.bubble) {
    t.bubble = at(el("p", { class: "reply session" }, el("span", { class: "body" })));
    t.log.append(t.bubble);
  }
  return t.bubble.querySelector<HTMLElement>(":scope > .body") || t.bubble;  // its words; its time stays beside them
}

// one line saying what the session is doing right now, replaced as it goes
// Muninn, listening beside the talk, did something about what was said: one line, apart from the session's
const beside = (text: string): HTMLElement => el("p", { class: "reply beside" }, el("span", { class: "who" }, "Muninn"), el("span", {}, text));
// what of Muninn's answer beside the talk the session was told, once it has heard it

// the session's steps this turn, as a small line that grows while it works and folds into a count when done
function step(t: Conversation, line: string): void {
  if (!t.steps) {
    const head = el("summary", {});
    const list = el("ol", {});
    const box = el("details", { class: "steps" }, head, list) as HTMLDetailsElement;
    t.log.append(box);
    t.steps = { box, head, list, n: 0 };
  }
  t.steps.n += 1;
  t.steps.list.append(el("li", {}, line));
  t.steps.head.textContent = w.talk.stepRunning(line);
}

function stepsDone(t: Conversation): void {
  if (!t.steps) return;
  t.steps.head.textContent = w.talk.stepsDone(t.steps.n);
  t.steps.box.classList.add("done");
  t.steps = null;
}

function setStatus(t: Conversation, text: string | null): void {
  if (!text) { if (t.status) t.status.remove(); t.status = null; return; }
  if (!t.status) t.status = el("p", { class: "thinking status" });
  t.status.textContent = text;
  t.log.append(t.status);  // stays last
}

// what the lent session's event stream says
type LeaseEvent =
  | { type: "text_delta"; text?: string }
  | { type: "assistant"; text: string }
  | { type: "tool_use"; kind?: StepKind; summary?: string }  // summary: one line saying what it did, as is
  | { type: "sidecar"; text: string }
  | { type: "relay"; text: string }
  | { type: "attachments"; paths?: string[] }  // where the session kept the files the user sent
  | { type: "turn_end" }
  | { type: "file"; id: string; name: string; mime: string; size: number; path?: string; summary?: string; lease_id?: string }
  | { type: "quota"; windows?: { id: string; label?: string; used: number }[] }
  | { type: "idle_warning" }
  | { type: "permission_card"; card: PermissionRequest }
  | { type: "permission_answered"; id: string; text?: string }
  | { type: "handover"; handover?: Handover }
  | { type: "error"; error: string; detail?: string }
  | { type: "state"; state: string; resumed?: boolean; detail?: string }
  | { type: "lease"; lease: { lease_id: string; project: string; label?: string; state: string; mode?: LeaseMode; can_work?: boolean; warn?: boolean; error?: string | null } };

function leaseEvent(t: Conversation, e: LeaseEvent): void {
  const scroll = () => scrollEnd(t);
  switch (e.type) {
    case "text_delta": setStatus(t, null); sessionBubble(t).textContent += e.text || ""; scroll(); return;
    case "assistant": setStatus(t, null); sessionBubble(t).replaceChildren(...markdown(e.text)); t.bubble = null; scroll(); return;
    case "tool_use": {
      const what = (e.summary || "").replace(w.talk.relayedAsk, "");  // the runner puts the owner's name before a question it relays
      setStatus(t, null);
      step(t, what ? (what.length > 60 ? what.slice(0, 59) + "…" : what) : w.talk.stepVerb[e.kind || "other"] || w.talk.stepVerb.other);
      scroll(); return;
    }
    case "turn_end": setStatus(t, null); stepsDone(t); return;
    case "file":
      t.log.append(at(shownFile({ role: "file", text: e.summary || "",
        files: [{ id: e.id, name: e.name, media_type: e.mime, lease: e.lease_id || (t.lease ? t.lease.lease_id : "") }] })));
      scroll(); return;
    case "sidecar": t.log.append(at(beside(e.text))); scroll(); return;
    case "relay": t.log.append(at(receipt(w.talk.told(called(t.lease), e.text)))); scroll(); return;
    case "attachments": if (e.paths && e.paths.length) { t.log.append(at(receipt(w.talk.stored(called(t.lease), e.paths.length), e.paths.join("\n")))); scroll(); } return;
    case "quota": {
      const hi = (e.windows || []).filter((x) => x.used >= 0.8);
      const note = hi.length ? w.talk.quotaUsed(hi.map((x) => `${x.label || x.id} ${Math.round(x.used * 100)}%`)) : "";
      if (note && note !== t.quotaNote) { t.quotaNote = note; t.log.append(el("p", { class: "thinking" }, note)); scroll(); }
      return;
    }
    case "idle_warning": setStatus(t, w.talk.idle); return;  // given back by itself
    case "permission_card": showAsk(t, e.card); scroll(); return;
    case "permission_answered": showAnswer(t, e.id, e.text); scroll(); return;
    case "handover": setStatus(t, null); stepsDone(t); t.log.append(at(handoverCard(e.handover || {}, t.lease ? called(t.lease) : null))); scroll(); return;
    case "error": setStatus(t, null); stepsDone(t); t.stuck = true; renderLeaseBar(t); t.log.append(el("p", { class: "muted" }, w.talk.sessionError(e.error, e.detail))); scroll(); return;
    case "lease":  // what is shown of the talk changed (a new name, its mode, its state): drawn from it whole
      if (t.lease && t.lease.lease_id === e.lease.lease_id) { Object.assign(t.lease, e.lease); renderLeaseBar(t); }
      return;
    case "state":
      if (t.lease) t.lease.state = e.state;
      if (e.resumed === false && e.detail) { t.log.append(el("p", { class: "thinking" }, w.talk.notResumed(e.detail))); scroll(); }
      if (e.state === "detached" && t.lease && t.resuming === t.lease.lease_id) {
        t.resuming = null;  // it goes on with the same session: this stream stays open for what it says next
        setStatus(t, w.talk.isThinking(called(t.lease)));
        return;
      }
      if (e.state === "detached") {
        if (t.stream) t.stream.close();
        t.stream = null;
        t.lease = null;
        setStatus(t, null);
        renderLeaseBar(t);
        t.log.append(el("p", { class: "thinking" }, w.talk.takenBack));
        scroll();
      }
  }
}

// a question or a web page the session asks about, answered in the conversation; the answer stays as a line
function showAsk(t: Conversation, p: PermissionRequest | undefined): void {
  if (!p || t.log.querySelector(`[data-pid="${CSS.escape(p.id)}"], [data-aid="${CSS.escape(p.id)}"]`)) return;
  const card = permissionCard(p, (note) => showAnswer(t, p.id, note));
  card.dataset.pid = p.id;
  t.log.append(card);
  if (t.status) t.log.append(t.status);
}

function showAnswer(t: Conversation, id: string, text?: string): void {
  if (t.log.querySelector(`[data-aid="${CSS.escape(id)}"]`)) return;
  const line = at(receipt(text || w.talk.answered));
  line.dataset.aid = id;
  const card = t.log.querySelector(`[data-pid="${CSS.escape(id)}"]`);
  if (card) card.replaceWith(line);
  else t.log.append(line);
}


async function toTerminal(t: Conversation): Promise<void> {
  try {
    const r = await api<{ command: string; hint?: string }>("/api/leases/terminal", { thread: t.thread });
    t.log.append(el("div", { class: "outcome" },
      el("span", { class: "text" }, r.hint || w.talk.terminalHint), el("code", {}, r.command),
      el("span", { class: "acts" }, el("button", { type: "button", class: "btn small", onclick: async () => {
        try { await navigator.clipboard.writeText(r.command); toast(w.talk.copied); } catch (e) { toast(w.talk.cannotCopy); }
      } }, w.talk.copy))));
    if (t.lease) t.lease.state = "terminal";
  } catch (err) { handleError(err); }
}

async function detach(t: Conversation): Promise<void> {
  try {
    await api("/api/leases/detach", { thread: t.thread });
    setStatus(t, w.talk.takingBack);
  } catch (err) { handleError(err); }
}

// a deep talk as it came back: what is the user's first (to do, still open), what was decided folded away
function handoverCard(h: Handover, project?: string | null): HTMLElement {
  const who = h.project || project || w.talk.project;
  const head = el("div", { class: "kicker" }, w.talk.doneWith(who));
  if (h.fallback) {
    return el("section", { class: "handover", "aria-label": w.talk.doneWith(who) }, head,
      el("p", { class: "muted" }, w.talk.noHandover));
  }
  const summary = h.summary ? el("p", { class: "summary clamp" }, h.summary) : null;
  if (summary && h.summary && h.summary.length > 90) {  // long: three lines, the rest a tap away
    summary.addEventListener("click", () => summary.classList.toggle("clamp"));
    summary.title = w.talk.seeAll;
  }
  const group = (label: string, items: Child[], shown = 3) => items.length ? el("div", { class: "group" },
    el("div", { class: "label" }, `${label} · ${items.length}`),
    el("ul", {}, items.slice(0, shown).map((x) => el("li", {}, x))),
    items.length > shown ? el("details", {}, el("summary", {}, w.talk.moreItems(items.length - shown)),
      el("ul", {}, items.slice(shown).map((x) => el("li", {}, x)))) : null) : null;
  const todo = (h.tasks_for_owner || []).map((x) => x.due ? w.talk.dueBy(x.text, x.due) : x.text);
  const decided = h.decisions || [];
  return el("section", { class: "handover", "aria-label": w.talk.doneWith(who) }, head, summary,
    group(w.talk.forYou, todo),
    group(w.talk.undecided, h.open_questions || []),
    decided.length ? el("details", { class: "decided" }, el("summary", {}, w.talk.decided(decided.length)),
      el("ul", {}, decided.map((d) => el("li", {}, d.what, d.why ? el("span", { class: "why" }, d.why) : null)))) : null);
}

// a lent session's relay asking: a question it put, or a web page outside the project's own sites.
// `answered` (in the conversation): the card turns into the line saying what was decided.
export function permissionCard(p: PermissionRequest, answered?: (note: string) => void): HTMLElement {
  const inline = !!answered;
  const decide = async (decision: string, answers?: Record<string, string | string[]>) => {
    try {
      const r = await api<{ note: string }>(`/api/permission-requests/${encodeURIComponent(p.id)}/decision`, { decision, answers });
      if (answered) { answered(r.note); render(); return; }  // the waiting copy of the card goes too
      toast(r.note || w.talk.answered);
    } catch (err) { handleError(err); }
    if (!inline) render();
  };
  const top = (text: string) => inline ? el("span", { class: "kicker" }, text) : el("div", { class: "card-top" }, el("span", {}, text));
  const box = (cls: string, label: string, ...children: Child[]) =>
    el(inline ? "div" : "section", { class: inline ? `ask${cls}` : `card review${cls}`, "aria-label": label }, ...children);
  const project = called(p);
  if (p.kind === "ask_user") {
    const qs = (p.question && p.question.questions) || [];
    const answers: Record<string, string | string[]> = {};
    const multi = qs.some((q) => q.multi);
    const done = el("button", { type: "button", class: "btn small", hidden: true, onclick: () => decide("allow", answers) }, w.talk.send);
    const ready = (): void => {  // every question has its answer: one choice sends at once; with several to pick, the send button does
      const all = qs.every((q) => { const a = answers[q.question]; return Array.isArray(a) ? a.length > 0 : !!a; });
      if (multi) done.hidden = !all; else if (all) decide("allow", answers);
    };
    const body = qs.map((q) => el("div", {}, el("p", { class: inline ? "ask-q" : "hero-text" }, q.question),
      el("div", { class: "options" }, (q.options || []).map((o) => el("button", { type: "button", "aria-pressed": "false",
        title: o.description || "", onclick: (ev) => {
        const b = (ev.currentTarget as HTMLElement);
        if (q.multi) {
          const picked = new Set(Array.isArray(answers[q.question]) ? answers[q.question] as string[] : []);
          if (picked.has(o.label)) picked.delete(o.label); else picked.add(o.label);
          answers[q.question] = [...picked];
          b.classList.toggle("suggested", picked.has(o.label));
          b.setAttribute("aria-pressed", String(picked.has(o.label)));
        } else {
          answers[q.question] = o.label;
          b.parentNode?.querySelectorAll("button").forEach((x) => x.classList.toggle("suggested", x === b));
        }
        ready();
      } }, o.label, o.description ? el("span", { class: "option-note" }, o.description) : null))),
      q.multi ? el("p", { class: "hint" }, w.talk.pickSeveral) : null));
    return box("", w.talk.asksYou(project), top(w.talk.asksYou(project)), ...body, multi ? done : null,
      el("button", { type: "button", class: "link-btn", onclick: () => decide("deny") }, w.talk.askInWords));
  }
  if (p.kind === "write_file" || p.kind === "run_command") {
    const write = p.kind === "write_file";
    return box("", write ? w.talk.wantsChangeFiles(project) : w.talk.wantsRunCommand(project),
      top(write ? w.talk.wantsChangeAFile(project) : w.talk.wantsRunACommand(project)),
      el("p", { class: "url" }, el("code", {}, p.summary || "")),
      el("div", { class: "options" },
        el("button", { type: "button", onclick: () => decide("allow") }, w.talk.allowOnce),
        el("button", { type: "button", class: "suggested", onclick: () => decide("deny") }, w.talk.deny),
        write && p.allow_session ? el("button", { type: "button", onclick: () => decide("allow_session") }, w.talk.allowFolder) : null));
  }
  if (p.kind !== "open_web") {  // something else it wants to do: said as it put it
    return box("", w.talk.wantsSomething(project), top(w.talk.wantsTo(project, p.kind)),
      el("p", { class: "url" }, el("code", {}, p.summary || "")),
      el("div", { class: "options" },
        el("button", { type: "button", onclick: () => decide("allow") }, w.talk.allowOnce),
        el("button", { type: "button", class: "suggested", onclick: () => decide("deny") }, w.talk.deny)));
  }
  const url = p.url || p.summary || "";
  const host = p.domain || "";
  const i = host ? url.indexOf(host) : -1;
  const before = i >= 0 ? url.slice(0, i) : "", tail = i >= 0 ? url.slice(i + host.length) : url;
  const q = tail.search(/[?#]/);
  return box(p.flagged ? " flagged" : "", w.talk.wantsWeb(project),
    top(w.talk.wantsAWebPage(project)),
    el("p", { class: "url" }, before, el("b", {}, host), q >= 0 ? tail.slice(0, q) : tail,
      q >= 0 ? el("span", { class: p.flagged ? "warn-text" : "" }, tail.slice(q)) : null),
    p.flagged ? el("p", { class: "warn-text" }, w.talk.flaggedUrl) : null,
    el("div", { class: "options" },
      el("button", { type: "button", onclick: () => decide("allow") }, w.talk.allowOnce),
      el("button", { type: "button", class: "suggested", onclick: () => decide("deny") }, w.talk.deny),
      p.allow_session ? el("button", { type: "button", onclick: () => decide("allow_session") }, w.talk.allowSite) : null));
}

// the next steps not drawn under a line Muninn said unasked: at the end
function unplaced(th: Thread): Thread["suggest"] {
  const lines = new Set(th.turns.filter((x) => x.spoken).map((x) => x.ts));
  return th.suggest.filter((s) => !s.turn || !lines.has(s.turn));
}

// the buttons of a conversation as the server has them now, every row of it drawn before gone (none on the screen is
// one already answered, or one it no longer has): each under the line Muninn said it with, when that line is on the
// screen; the rest after `end` (what the round just said), else where the last round's own were
function placeButtons(thread: string | null, list: Suggest[] | undefined, end?: Node | null): void {
  const log = talk?.log;
  if (!log || !thread) return;
  const old = Array.from(log.querySelectorAll<HTMLElement>(".suggest")).filter((r) => r.dataset.thread === thread);
  const loose = old.find((r) => !r.dataset.turn);
  const lineOf = (turn?: string) => turn ? log.querySelector(`.reply[data-ts="${new Date(turn).toISOString()}"]`) : null;
  const byLine = new Map<string, Suggest[]>();
  const rest: Suggest[] = [];
  for (const s of list || []) {
    if (s.turn && lineOf(s.turn)) byLine.set(s.turn, [...(byLine.get(s.turn) || []), s]);
    else rest.push(s);
  }
  for (const [turn, items] of byLine) lineOf(turn)?.after(suggestRow(items, thread, turn)!);
  const row = suggestRow(rest, thread);
  if (row) {
    if (end) (end as ChildNode).after(row);
    else if (loose) loose.before(row);
    else log.append(row);
  }
  for (const r of old) r.remove();
}

type Suggest = NonNullable<CommandReply["suggest"]>[number];
const pressKey = (s: Suggest): string => s.id || `#${s.index}`;

// whether the conversation's buttons on the screen are not the ones the server offers now
function stalePresses(th: Thread): boolean {
  const shown = Array.from(talk?.log.querySelectorAll<HTMLElement>(".suggest") || []).filter((r) => r.dataset.thread === th.thread)
    .flatMap((r) => Array.from(r.querySelectorAll<HTMLElement>("button[data-press]")).map((b) => b.dataset.press || ""));
  if (!shown.length) return false;  // none drawn: new ones come with what Muninn says
  const now = new Set(th.suggest.map(pressKey));
  return shown.length !== now.size || shown.some((k) => !now.has(k));
}

function suggestRow(list: CommandReply["suggest"], thread: string | null, turn?: string): HTMLElement | null {
  if (!list || !list.length || !thread) return null;
  const row = el("div", { class: "suggest", "data-thread": thread, "data-turn": turn }, el("div", { class: "options" }, list.map((x, i) => el("button", { type: "button", class: i === 0 && !x.plain ? "suggested" : "", "data-press": pressKey(x), onclick: async () => {
    row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const r = await api<CommandReply>("/api/command/act", { thread, id: x.id, index: x.index });
      const nodes = [el("div", { class: "said" }, w.talk.pressed(x.label)), ...turnNodes(r)];
      row.replaceWith(...nodes);
      placeButtons(thread, r.suggest, nodes[nodes.length - 1]);
      if (r.lent && talk) lent(talk, r.lent);
      render();
    } catch (err) { handleError(err); }
    if (talk) scrollEnd(talk);
  } }, x.label))));
  return row;
}

function turnNodes(r: CommandReply): HTMLElement[] {
  return kids([
    r.reply ? at(el("p", { class: "reply" }, r.reply)) : null,
    r.stood_in ? el("p", { class: "note stood-in" }, w.talk.stoodIn(cloudName())) : null,
    ...(r.outcomes ?? []).map((o) => el("div", { class: "outcome" },
      el("span", { class: "text" }, o.text),
      el("span", { class: "acts" },
        ...(o.confirm_id ? (o.options || []).map((opt, i) => el("button", { type: "button", class: i === 0 ? "btn small" : "link-btn", onclick: async (ev) => {
          await api(`/api/confirmations/${o.confirm_id}`, { answer: opt });
          (ev.target as HTMLElement).parentNode?.replaceChildren(el("span", { class: "muted" }, opt));
          render();
        } }, opt)) : []),
        o.handoff_id ? el("button", { type: "button", class: "link-btn", onclick: async (ev) => {
          await api(`/api/handoffs/${o.handoff_id}/cancel`, {});
          (ev.target as HTMLElement).replaceWith(el("span", { class: "muted" }, w.talk.notHandedOff));
        } }, w.talk.dontHandOff) : null,
        o.link ? el("a", { class: "link-btn", href: o.link }, w.talk.open) : null))),
  ]) as HTMLElement[];
}
