// How dates, people and models read on screen.

import { w } from "./words.js";

// read once, when the page loads: the page's language is fixed until it is reloaded
export const WEEKDAY: readonly string[] = w.format.weekdays;
export const fmtDay = (d: Date): string => w.format.day(d.getMonth() + 1, d.getDate(), WEEKDAY[d.getDay()] ?? "");
export const fmtTime = (iso: string): string => new Date(iso).toLocaleTimeString(w.format.timeLocale, { hour: "2-digit", minute: "2-digit", hour12: false });
export const fmtMD = (iso: string): string => { const d = new Date(iso); return `${d.getMonth() + 1}/${d.getDate()}`; };
// "Name <address>" → "Name"
// what the user calls a project: an idea's sandbox goes by the idea's name
export const called = (x: { project?: string | null; label?: string | null } | null | undefined, none = w.format.project): string =>
  (x && (x.label || x.project)) || none;

export const sender = (from: string | null | undefined): string => (from || "").replace(/<.*>/, "").trim();

export const PROVIDER: Record<string, string> = { google: "Google", microsoft: "Microsoft" };
export const MAIL_LABEL: Record<string, string> = w.format.mailLabel;
