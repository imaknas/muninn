// The cards the conversation and the panel show: each asks one thing and carries out the answer (taken back by saying undo).

import { api } from "./api.js";
import { el, ICON, kids, svg, toast } from "./dom.js";
import { called, fmtTime, PROVIDER } from "./format.js";
import { answered, handleError, prompt, reader, render } from "./shell.js";
import { openTalk, setTopic } from "./talk.js";
import { w } from "./words.js";
import type { About, ActiveLease, CommandReply, Confirmation, Idea, Place, ProjectQuestion, ReviewItem, Talk, Task, Undoable, Waiting } from "./types.js";

// what was done, said once; taking it back is saying so
const undoToast = (r: Undoable) => toast(r.text);

export async function connectAccount(name: string): Promise<void> {
  const r = await api<{ url: string }>(`/api/connections/${name}`, {});
  location.href = r.url;
}

export async function replan(): Promise<void> {
  const r = await api<{ blocks: unknown[] }>("/api/schedule", {});
  toast(r.blocks.length ? w.cards.replanned(r.blocks.length) : w.cards.noGapToday);
  render();
}

export async function copy(text: string, what?: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast(w.cards.copied(what));
  } catch (e) {
    toast(w.cards.cannotCopy);
  }
}

export function relinkCard(name: string): HTMLElement {
  return el("div", { class: "card relink" },
    el("div", { class: "status" }, el("span", { class: "icon warn" }, svg(ICON.warn)),
      el("div", {}, el("b", {}, w.cards.authExpired(PROVIDER[name] || name)), el("span", {}, w.cards.pausedUntilRelinked))),
    el("button", { type: "button", class: "btn primary block", onclick: () => connectAccount(name) }, w.cards.relink(PROVIDER[name] || name)));
}

// one plan item to confirm; `items` are the ones waiting, this one first (the desktop passes it alone);
// `sameDomain`: how many wait in its domain, all of them (two or more get the button accepting all of it as drafted)
export function reviewCard(items: ReviewItem[], sameDomain = items.filter((x) => x.domain === items[0]?.domain).length): HTMLElement | null {
  const it = items[0];
  if (!it) return null;
  const card = el("section", { class: "card review", "aria-label": w.cards.confirmWithYou });
  const done = async (decision: string, value?: string) => {
    undoToast(await api<Undoable>("/api/review", { id: it.id, decision, value }));
    render();
  };
  const changeForm = el("form", { class: "name-place", hidden: true, onsubmit: (e) => {
    e.preventDefault();
    done("change", String(new FormData(e.target as HTMLFormElement).get("value")));
  } },
    it.kind === "goal"
      ? el("input", { name: "value", type: "number", step: "0.5", inputmode: "decimal", value: String(it.value), "aria-label": w.cards.newNumber, required: true })
      : el("input", { name: "value", type: "date", value: String(it.value), "aria-label": w.cards.newDate, required: true }),
    el("button", { type: "submit", class: "btn small" }, w.cards.changeDone));
  const why = el("p", { class: "sub", hidden: true });
  const about: About = { kind: "review", id: it.id };
  const title = w.cards.itemTitle(it.title, it.detail);
  card.append(...kids([
    el("div", { class: "card-top" }, el("span", {}, w.cards.confirmIn(it.domain_title)), items.length > 1 ? el("span", {}, w.cards.more(items.length - 1)) : null),
    el("p", { class: "hero-text" }, title),
    el("p", { class: "sub" }, it.source
      ? w.cards.source(it.source)
      : (it.kind === "goal" ? w.cards.goalNotDecided : w.cards.deadlineNotConfirmed)),
    it.actions.length ? el("p", { class: "note" }, w.cards.ifBehind(it.actions)) : null,
    why,
    afterTalk(it.talk || null, about, title),
    el("div", { class: "options" },
      el("button", { type: "button", class: "suggested", onclick: () => done("confirm") }, w.cards.yes),
      el("button", { type: "button", onclick: () => { changeForm.hidden = !changeForm.hidden; } }, it.kind === "goal" ? w.cards.changeNumber : w.cards.changeDate),
      el("button", { type: "button", onclick: () => done("reject") }, w.cards.no),
      it.talk ? null : el("button", { type: "button", onclick: () => openTalk(about, title) }, w.cards.discuss),
      el("button", { type: "button", onclick: async (e) => {
        (e.target as HTMLButtonElement).disabled = true;
        why.hidden = false;
        why.textContent = w.cards.lookingWhy;
        why.textContent = (await api<{ answer: string }>("/api/review/why", { id: it.id })).answer;
      } }, w.cards.why),
      sameDomain > 1 ? el("button", { type: "button", onclick: async () => {
        undoToast(await api<Undoable>("/api/review/domain", { domain: it.domain }));
        render();
      } }, w.cards.acceptDomain(it.domain_title, sameDomain)) : null),
    changeForm]));
  return card;
}

// the server's answer for "none of these" to a question of where activity belongs (core.engine.NONE_OF_THESE)
const NONE_OF_THESE = "\u90fd\u4e0d\u662f";

export function questionCard(c: Confirmation): HTMLElement {
  const data = c.data || {};
  const about: About = { kind: "question", id: c.id };
  return el("section", { class: "card review", "aria-label": w.cards.askYou },
    el("div", { class: "card-top" }, el("span", {}, w.cards.asks[c.kind] || w.cards.askYou)),
    el("p", { class: "hero-text" }, c.question),
    data.why ? el("p", { class: "sub" }, data.why) : null,
    afterTalk(c.talk, about, c.question),
    el("div", { class: "options" },
      ...c.options.map((o, i) => el("button", { type: "button", class: i === 0 && !data.neutral ? "suggested" : "",
        onclick: (ev: Event) => answered(ev.currentTarget as Element, () => api(`/api/confirmations/${c.id}`, { answer: o }), w.cards.noted) }, o)),
      c.talk ? null : el("button", { type: "button", onclick: () => openTalk(about, c.question) }, w.cards.discuss)));
}

// a question with the answers to pick from (which repo or window belongs where, a plan change, ...)
export function confirmationCard(c: Confirmation): HTMLElement {
  const data = c.data || {};
  const titles = data.titles || {};
  const opts = c.kind === "attribution" ? c.options.filter((o) => o !== NONE_OF_THESE) : c.options;
  return el("section", { class: "card", "aria-label": w.cards.toAnswer },
    el("div", { class: "card-top" }, el("span", {}, w.cards.confirmationKinds[c.kind] || w.cards.toAnswer),
      data.observations ? el("span", {}, w.cards.unattributed(data.observations)) : null),
    data.match ? el("p", { class: "question" }, w.cards.whereBefore || null, el("span", { class: "mono" }, data.match), w.cards.whereAfter) : el("p", { class: "question" }, c.question),
    data.suggestion ? el("p", { class: "sub" }, w.cards.suggests(reader(), titles[data.suggestion] || data.suggestion)) : null,
    el("div", { class: "options" }, opts.map((o) => el("button", {
      type: "button", class: o === data.suggestion ? "suggested" : "",
      onclick: (ev: Event) => answered(ev.currentTarget as Element, () => api(`/api/confirmations/${c.id}`, { answer: o }), w.cards.noted),
    }, titles[o] || o))),
    c.kind === "attribution" ? el("button", { type: "button", class: "btn dashed block", onclick: (ev: Event) => answered(ev.currentTarget as Element, () => api(`/api/confirmations/${c.id}`, { answer: NONE_OF_THESE }), w.cards.keptSensitive) },
      w.cards.noneOfThese) : null);
}

// a conversation lent to a project's session: the way back into it
export function leaseCard(l: ActiveLease): HTMLElement {
  return el("section", { class: "card lease-card", "aria-label": w.cards.inDeepTalkLabel(called(l)) },
    el("div", { class: "status" }, el("div", {}, el("b", {}, w.cards.inDeepTalk(called(l))),
      el("span", {}, l.warn ? w.cards.idleHandingBack : l.state === "terminal" ? w.cards.inTerminal : w.cards.readOnly))),
    el("button", { type: "button", class: "btn block", onclick: () => openTalk(null, null, null, { thread: l.thread }) }, w.cards.backToConversation));
}

export async function dismissQuestion(q: ProjectQuestion): Promise<void> {
  try {
    await api(`/api/project-questions/${encodeURIComponent(q.id)}/dismiss`, {});
    toast(w.cards.dismissed(q.project));
  } catch (err) { handleError(err); }
  render();
}

async function answerQuestion(q: ProjectQuestion, box: HTMLTextAreaElement): Promise<void> {
  const answer = box.value.trim();
  if (!answer) { toast(w.cards.writeAnswerFirst); box.focus(); return; }
  await api(`/api/project-questions/${encodeURIComponent(q.id)}/answer`, { answer });
  toast(w.cards.forwarded(q.project));
  render();
}

// what a deep-talk left open, together: answer one, let one go, or reread the conversation
// what a deep talk left undecided: asked in the talk, so the way there is the one thing to do here
export function talkQuestionsCard(wt: Waiting): HTMLElement {
  const project = wt.by || w.cards.someProject;
  return el("section", { class: "card review", "aria-label": w.cards.talkQuestionsLabel(project) },
    el("div", { class: "card-top" }, el("span", {}, w.cards.talkLeftQuestions(project, wt.lines.length))),
    ...wt.lines.map((q) => el("div", { class: "talk-q" }, el("p", {}, q))),
    el("p", { class: "sub" }, w.cards.talkWillAsk),
    el("div", { class: "options" }, el("button", { type: "button", class: "suggested",
      onclick: () => openTalk(null, w.cards.talkWith(project), null, { thread: wt.thread || "", lease_id: wt.lease }) }, w.cards.backToDeepTalk)));
}

// a project asking the user something: the answer goes back to that project
export function projectQuestionCard(q: ProjectQuestion): HTMLElement {
  const box = el("textarea", { placeholder: w.cards.answerPlaceholder, "aria-label": w.cards.answerLabel(q.project) });
  const about: About = { kind: "project_question", id: q.id };
  const title = w.cards.projectAsksTitle(q.project, q.text);
  return el("section", { class: "card review", "aria-label": w.cards.projectAsks(q.project) },
    el("div", { class: "card-top" }, el("span", {}, w.cards.projectAsks(q.project))),
    el("p", { class: "hero-text" }, q.text),
    afterTalk(q.talk, about, title),
    box,
    el("button", { type: "button", class: "btn primary block", onclick: () => answerQuestion(q, box) }, w.cards.reply),
    q.talk ? null : el("button", { type: "button", class: "btn block", onclick: () => openTalk(about, title) }, w.cards.discuss),
    el("button", { type: "button", class: "link-btn", onclick: () => dismissQuestion(q) }, w.cards.skipNoAnswer));
}

// done | dropped
export async function closeTask(t: Task, status: "done" | "dropped"): Promise<void> {
  undoToast(await api<Undoable>(`/api/tasks/${encodeURIComponent(t.key)}/close`, { status }));
  render();
}

export function taskRow(t: Task): HTMLElement {
  const about: About = { kind: "task", id: t.key };
  return el("div", { class: "task-row", role: "group", "aria-label": t.title },
    el("div", { class: "body" },
      el("span", { class: "title" }, t.title),
      el("span", { class: "meta" }, [t.due ? w.cards.dueBy(fmtDue(t.due)) : null, t.projects.length ? t.projects.join(w.cards.listSeparator) : "Muninn", `T-${t.n}`]
        .filter(Boolean).join(" · "))),
    afterTalk(t.talk, about, t.title),
    el("div", { class: "options" },
      t.mail ? el("a", { class: "btn", href: `#mail/${encodeURIComponent(t.mail)}` }, w.cards.readMail) : null,
      el("button", { type: "button", class: "suggested", onclick: () => closeTask(t, "done") }, w.cards.done),
      el("button", { type: "button", onclick: () => closeTask(t, "dropped") }, w.cards.notNeeded),
      t.talk ? null : el("button", { type: "button", onclick: () => openTalk(about, t.title) }, w.cards.discuss)));
}

// an idea kept by saying so: what it is, when, and talking it through
export function ideaRow(i: Idea): HTMLElement {
  const about: About = { kind: "idea", id: i.n };
  return el("div", { class: "task-row", role: "group", "aria-label": i.text },
    el("div", { class: "body" },
      el("span", { class: "title" }, i.text),
      el("span", { class: "meta" }, w.cards.ideaMeta(fmtDue(i.ts), i.n, i.status === "shelved"))),
    afterTalk(i.talk, about, i.text),
    el("div", { class: "options" },
      i.talk ? null : el("button", { type: "button", onclick: () => openTalk(about, i.text) }, w.cards.discuss),
      ...ideaDecisions(i).map(([label, decision]) => el("button", { type: "button", onclick: () => settleIdea(i, decision) }, label))));
}

// what can be decided about an idea from a button, as from saying so
export function ideaDecisions(i: Idea): [string, string][] {
  return i.status === "shelved" ? [[w.cards.bringBack, "open"]] : [[w.cards.shelve, "shelved"], [w.cards.drop, "dropped"]];
}

export async function settleIdea(i: Idea, decision: string): Promise<void> {
  const r = await api<Partial<Undoable>>(`/api/ideas/${i.n}/settle`, { decision });
  if (r.text && r.action_id) undoToast(r as Undoable);
  render();
}

const fmtDue = (due: string) => w.cards.monthDay(new Date(`${due.slice(0, 10)}T00:00`));

// what a discussion about this item left: Muninn's last word and the next steps it offered
export function afterTalk(talk: Talk | null, about: About, title: string): HTMLElement | null {
  if (!talk) return null;
  return el("div", { class: "after-talk" },
    el("span", { class: "kicker" }, w.cards.afterTalk),
    talk.last ? el("p", { class: "sub clamp" }, talk.last) : null,
    talk.suggest.length ? el("div", { class: "options" }, talk.suggest.map((x, i) => el("button", { type: "button", class: i === 0 ? "suggested" : "",
      onclick: async () => {
        const r = await api<CommandReply>("/api/command/act", { thread: talk.thread, id: x.id, index: x.index ?? i });
        toast((r.outcomes ?? []).map((o) => o.text).join(w.cards.outcomeSeparator) || w.cards.okay);
        if (r.lent) openTalk(about, title, null, talk);  // it went on with a sandbox: the conversation is where it goes on
        render();
      } }, x.label))) : null,
    el("button", { type: "button", class: "link-btn", onclick: () => openTalk(about, title, null, talk) }, w.cards.keepDiscussing));
}

// the places of a day, each unnamed one with a form to name it
export function placeRows(places: Place[]): HTMLElement {
  return el("div", {}, places.map((p, i) => el("div", { class: "place-row" },
    el("div", { class: "rule" },
      el("span", { class: "title" }, p.place || w.cards.unnamedPlace),
      el("span", { class: "value num" }, i === places.length - 1 ? w.cards.sinceNow(fmtTime(p.start)) : `${fmtTime(p.start)}–${fmtTime(p.end)} · ${p.hours}h`)),
    p.place ? null : el("form", { class: "name-place", onsubmit: async (e) => {
      e.preventDefault();
      const name = String(new FormData(e.target as HTMLFormElement).get("name"));
      undoToast(await api<Undoable>("/api/places", { name, lat: p.lat, lon: p.lon }));  // the same action as saying it
      render();
    } },
      el("a", { class: "link-btn", href: `https://maps.apple.com/?ll=${p.lat},${p.lon}&q=${encodeURIComponent(w.cards.here)}`, target: "_blank", rel: "noopener" }, w.cards.map),
      el("input", { name: "name", placeholder: w.cards.namePlaceholder, required: true, "aria-label": w.cards.placeName }),
      el("button", { type: "submit", class: "btn small" }, w.cards.remember)))));
}


// a new user with no domain yet: where to start (the conversation drafts the first one, each item waiting for a yes)
export function startCard(): HTMLElement {
  return el("section", { class: "card review start-card", "aria-label": w.cards.startHere },
    el("div", { class: "card-top" }, el("span", {}, w.cards.startHere)),
    el("p", { class: "hero-text" }, w.cards.meetYou),
    el("p", { class: "sub" }, w.cards.noDomainYet),
    el("div", { class: "options" },
      el("button", { type: "button", class: "suggested", onclick: () => {
        setTopic({ kind: "onboarding", id: "first" }, w.cards.firstDomain);  // what follows drafts the first domains
        prompt(w.cards.careAbout);
      } }, w.cards.start),
      // what Muninn sees comes from the accounts and sources set up there; without them it measures nothing
      el("a", { class: "btn", href: "#settings" }, w.cards.connectSources)),
    el("p", { class: "note" }, w.cards.connectNote));
}
