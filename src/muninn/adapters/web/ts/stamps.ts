// When each line of the conversation was said: a 12-hour time beside it, and where the day changes a line saying
// which day it is (the date never sits beside the time).

import { el } from "./dom.js";
import { WEEKDAY } from "./format.js";
import { w } from "./words.js";

export const clock = (d: Date): string => w.stamps.clock(d);

// `node` said at `ts` (now when there is none: it is being said now), with its time beside it
export function at<T extends HTMLElement>(node: T, ts?: string | null): T {
  const when = ts ? new Date(ts) : new Date();
  if (Number.isNaN(when.getTime()) || node.dataset.ts) return node;
  node.dataset.ts = when.toISOString();
  for (const t of Array.from(node.childNodes)) {  // its words stay a piece of their own, apart from the time
    if (t.nodeType === Node.TEXT_NODE && t.textContent?.trim()) t.replaceWith(el("span", {}, t.textContent));
  }
  const stamp = el("time", { class: "stamp", datetime: when.toISOString() }, clock(when));
  const summary = node.querySelector(":scope > summary");  // folded: beside the line that stays shown
  (summary || node).append(stamp);
  return node;
}

const dayKey = (d: Date): string => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

export function dayName(d: Date, now = new Date()): string {
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(d)) / 86_400_000);
  if (days === 0) return w.stamps.today;
  if (days === 1) return w.stamps.yesterday;
  return w.stamps.day(d, WEEKDAY[d.getDay()] ?? "", d.getFullYear() !== now.getFullYear());
}

// a line saying the day before the first thing said on each day (drawn again from the times, so it is always right)
export function days(log: HTMLElement): void {
  log.querySelectorAll(":scope > .day").forEach((x) => x.remove());
  let last = "";
  for (const node of Array.from(log.querySelectorAll<HTMLElement>(":scope > [data-ts]"))) {
    const d = new Date(node.dataset.ts || "");
    const key = dayKey(d);
    if (key === last) continue;
    last = key;
    let first: Element = node;  // above the name of the topic it opens, when there is one
    while (first.previousElementSibling?.matches(".divider")) first = first.previousElementSibling;
    first.before(el("div", { class: "day", role: "separator" }, el("span", {}, dayName(d))));
  }
}

// what was said before today, folded into one line at the top: the conversation opens on today. Every line from an
// earlier day is folded wherever it sits (a conversation said in again today brings its older lines along); the name
// of a topic goes with its lines, into the fold as well when some of them are from today.
export function foldEarlier(log: HTMLElement, now = new Date()): void {
  const today = dayKey(now);
  log.querySelector(":scope > details.earlier")?.remove();
  const lines = Array.from(log.children).filter((n) => !n.matches(".day")) as HTMLElement[];
  const before = (n: HTMLElement): boolean => !!n.dataset.ts && dayKey(new Date(n.dataset.ts)) !== today;
  const earlier: HTMLElement[] = [];
  let topic: HTMLElement[] = [];  // one topic: its name, then what was said in it
  const fold = () => {
    const named = topic[0]?.matches(".divider") ? topic[0] : null;
    const said = named ? topic.slice(1) : topic;
    // a line without a time of its own (the buttons a reply offered) goes with the line before it; one with nothing
    // before it, with the first that has a time
    const first = said.find((n) => n.dataset.ts);
    const old: HTMLElement[] = [];
    let prev = first ? before(first) : false;
    for (const n of said) {
      if (n.dataset.ts) prev = before(n);
      if (prev) old.push(n);
    }
    if (!old.length) return;
    if (named) earlier.push(old.length === said.length ? named : named.cloneNode(true) as HTMLElement);
    earlier.push(...old);
  };
  for (const n of lines) {
    if (n.matches(".divider") && topic.length) { fold(); topic = []; }
    topic.push(n);
  }
  fold();
  const said = earlier.filter((n) => n.dataset.ts).length;
  if (!said) return;
  const inner = el("div", { class: "earlier-lines" }, ...earlier);
  days(inner);
  log.prepend(el("details", { class: "earlier" }, el("summary", {}, w.stamps.earlier(said)), inner));
  days(log);
}
