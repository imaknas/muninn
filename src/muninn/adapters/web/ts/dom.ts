// Building the page: elements from plain values, never from HTML strings (except the static icons below).

export type Child = Node | string | number | null | undefined | false | readonly Child[];
type Handler = (e: Event) => unknown;
export type Attrs = Record<string, string | number | boolean | null | undefined | Handler>;

export function $<T extends HTMLElement = HTMLElement>(sel: string, root: ParentNode = document): T | null {
  return root.querySelector<T>(sel);
}

// an element the page always has (index.html); its absence is a bug, not a state to handle
export function must<T extends HTMLElement = HTMLElement>(sel: string, root: ParentNode = document): T {
  const n = root.querySelector<T>(sel);
  if (!n) throw new Error(`missing ${sel}`);
  return n;
}

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (typeof v === "function") n.addEventListener(k.slice(2), v as EventListener);
    else if (k === "class") n.className = String(v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  n.append(...kids(children));
  return n;
}

// the browser's own append() prints null as the text "null": everything goes through this first
export function kids(list: readonly Child[]): (Node | string)[] {
  const out: (Node | string)[] = [];
  for (const c of list) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) out.push(...kids(c));
    else out.push(typeof c === "number" ? String(c) : (c as Node | string));
  }
  return out;
}

export function svg(paths: string): SVGSVGElement {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = paths; // static, author-written icon paths only
  return s;
}

export const ICON = {
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"></path>',
  warn: '<path d="M12 8v5M12 16.5v.5"></path><circle cx="12" cy="12" r="9"></circle>',
  back: '<path d="M15 6l-6 6 6 6"></path>',
  out: '<path d="M14 5h5v5M19 5l-8 8M10 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4"></path>',
} as const;

export interface ToastAction { label: string; run: () => unknown }

let toastTimer: ReturnType<typeof setTimeout> | undefined;
export function toast(text: string, action?: ToastAction): void {
  const t = must("#toast");
  t.replaceChildren(el("span", {}, text));
  if (action) t.append(el("button", { type: "button", onclick: () => { t.hidden = true; action.run(); } }, action.label));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), action ? 8000 : 2500);
}

export function head(title: string, kicker: string | null): HTMLElement {
  return el("header", { class: "page-head" },
    el("div", { class: "stack" }, kicker ? el("span", { class: "kicker" }, kicker) : null, el("h1", {}, title)));
}

export function section(label: string, ...children: Child[]): HTMLElement {
  return el("section", { class: "section", "aria-label": label }, ...children);
}

export function sectionHead(label: string, hint?: string): HTMLElement {
  return el("div", { class: "section-head" }, el("h2", {}, label), hint ? el("span", { class: "hint" }, hint) : null);
}

// the value of the form control an event came from
export const valueOf = (e: Event): string => (e.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
