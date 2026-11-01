// First-run setup, reached by the one-time link `muninn serve` prints (…/#setup=<token>) while no device is
// paired: which service answers, its key, its models, then the phone's pairing by QR code. The token goes in a
// header, never in a URL the server sees.

import { backUp } from "./api.js";
import type { SetupModels, SetupPair, SetupSaved, SetupState } from "./types.js";
import { el, kids, must, type Child } from "./dom.js";
import { LANGUAGE_NAMES, LANGUAGES, lang, useLanguage, w, type Lang } from "./words.js";

async function call<T>(token: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST", credentials: "same-origin",
    headers: { "X-Setup-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = (await r.json().catch(() => ({}))) as T & { error?: string };
  if (!r.ok) throw new Error(data.error || (r.status === 403 ? w.setup.linkUsed : w.setup.http(r.status)));
  return data;
}

type Choice = { impl: string; key?: string; base_url?: string; label?: string };

function screen(...children: Child[]): void {
  must("#app").hidden = true;
  const root = must("#pair");
  root.hidden = false;
  root.replaceChildren(...kids([el("h1", {}, w.setup.title), ...children]));
}

export async function renderSetup(token: string): Promise<void> {
  let state: SetupState;
  try {
    state = await call<SetupState>(token, "/api/setup");
  } catch (err) {
    screen(el("p", { class: "error" }, err instanceof Error ? err.message : String(err)));
    return;
  }
  chooseService(token, state);
}

// the language to set up in: the page shows it at once, and saving the model writes it into config
function languages(redraw: () => void): HTMLElement {
  return el("p", { class: "choice-row", role: "group", "aria-label": w.setup.language },
    ...(Object.keys(LANGUAGES) as Lang[]).map((code) => el("button", {
      type: "button", class: code === lang ? "btn small primary" : "btn small", "aria-pressed": code === lang ? "true" : "false",
      onclick: () => { useLanguage(code); redraw(); } }, LANGUAGE_NAMES[code])));
}

function chooseService(token: string, state: SetupState, error?: string): void {
  const form = el("form", { class: "pair-form", onsubmit: async (e) => {
    e.preventDefault();
    const f = new FormData(e.target as HTMLFormElement);
    const choice: Choice = { impl: String(f.get("impl")) };
    for (const k of ["key", "base_url", "label"] as const) if (f.get(k)) choice[k] = String(f.get(k));
    try {
      const { models } = await call<SetupModels>(token, "/api/setup/models", choice);
      chooseModels(token, state, choice, models);
    } catch (err) {
      chooseService(token, state, err instanceof Error ? err.message : String(err));
    }
  } },
    ...state.choices.map((c, i) => el("label", { class: "choice" },
      el("input", { type: "radio", name: "impl", value: c.impl, checked: i === 0, required: true }), " ", w.setup.choices[c.impl] || c.impl)),
    el("input", { name: "key", type: "password", placeholder: w.setup.keyPlaceholder, autocomplete: "off", "aria-label": w.setup.keyLabel }),
    el("input", { name: "base_url", placeholder: w.setup.urlPlaceholder, "aria-label": w.setup.urlLabel }),
    el("input", { name: "label", placeholder: w.setup.namePlaceholder, "aria-label": w.setup.nameLabel }),
    el("button", { type: "submit", class: "btn primary block" }, w.setup.checkKey));
  screen(
    languages(() => chooseService(token, state, error)),
    el("p", { class: "sub" }, w.setup.pickService),
    state.has_model ? el("p", { class: "note" }, w.setup.hasModel) : null,
    form, error ? el("p", { class: "error" }, error) : null,
    el("button", { type: "button", class: "link-btn", onclick: () => pairPhone(token, state) }, w.setup.skip));
}

function chooseModels(token: string, state: SetupState, choice: Choice, models: string[], error?: string): void {
  const local = choice.impl === "ollama";
  const pick = (name: string, label: string) => models.length
    ? el("label", {}, label, el("select", { name, required: true }, ...models.map((m) => el("option", { value: m }, m))))
    : el("input", { name, required: true, placeholder: w.setup.modelId(label), "aria-label": label });
  screen(
    el("p", { class: "sub" }, local ? w.setup.pickLocal : w.setup.pickTwo),
    local ? null : el("p", { class: "note" }, w.setup.cloudReadsAll(choice.label || w.setup.choices[choice.impl as keyof typeof w.setup.choices] || choice.impl)),
    el("form", { class: "pair-form", onsubmit: async (e) => {
      e.preventDefault();
      const f = new FormData(e.target as HTMLFormElement);
      const button = (e.target as HTMLFormElement).querySelector<HTMLButtonElement>("button[type=submit]");
      if (button) { button.disabled = true; button.textContent = w.setup.checking; }  // the model is asked once first
      try {
        const saved = await call<SetupSaved>(token, "/api/setup/save",
          { ...choice, key: undefined, fast: f.get("fast"), standard: local ? undefined : f.get("standard"), language: lang });
        if (saved.restarting) await restarted();
        else screen(el("p", { class: "sub" }, w.setup.savedRestartYourself));
        if (saved.restarting) pairPhone(token, state);
      } catch (err) {
        chooseModels(token, state, choice, models, err instanceof Error ? err.message : String(err));
      }
    } },
      pick("fast", local ? w.setup.localModel : w.setup.fastModel),
      local ? null : pick("standard", w.setup.goodModel),
      el("button", { type: "submit", class: "btn primary block" }, w.setup.save)),
    error ? el("p", { class: "error" }, error) : null);
}

async function restarted(): Promise<void> {
  screen(el("p", { class: "sub" }, w.setup.restarting));
  await backUp();
}

// this computer's own browser (the page was opened at 127.0.0.1 or localhost): it can pair without HTTPS
const onThisComputer = (): boolean => ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);

async function useHere(token: string): Promise<void> {
  const { code } = await call<{ code: string }>(token, "/api/setup/pair-here", {});
  const r = await fetch("/api/pair", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                                      body: JSON.stringify({ code, name: w.setup.thisComputer }) });
  if (!r.ok) throw new Error(w.setup.http(r.status));
  location.replace("/");  // the app, as this browser now opens it
}

function hereButton(token: string): HTMLElement | null {
  if (!onThisComputer()) return null;
  const b = el("button", { type: "button", class: "btn primary block", onclick: async () => {
    b.disabled = true;
    try { await useHere(token); } catch (err) { b.disabled = false; b.after(el("p", { class: "error" }, err instanceof Error ? err.message : String(err))); }
  } }, w.setup.useHere);
  return b;
}

async function pairPhone(token: string, state: SetupState): Promise<void> {
  let pair: SetupPair;
  try {
    pair = await call<SetupPair>(token, "/api/setup/pair", {});
  } catch (err) {
    screen(el("p", { class: "sub" }, w.setup.lastStep),
      hereButton(token),
      onThisComputer() ? el("p", { class: "note" }, w.setup.phoneLater) : null,
      el("p", { class: "error" }, err instanceof Error ? err.message : String(err)),
      el("p", { class: "note" }, w.setup.needsHttps(location.port)),
      state.public_url ? null : el("button", { type: "button", class: "link-btn", onclick: () => pairPhone(token, state) }, w.setup.tryAgain));
    return;
  }
  screen(
    hereButton(token),
    el("p", { class: "sub" }, w.setup.scan),
    el("img", { src: `data:image/svg+xml;base64,${btoa(pair.qr)}`, alt: w.setup.qrAlt(pair.url), class: "pair-qr", width: 240, height: 240 }),
    el("p", { class: "note" }, w.setup.cannotScan, el("code", {}, pair.url.split("?")[0]), w.setup.thenEnter, el("b", {}, pair.code), w.setup.sentenceEnd),
    el("button", { type: "button", class: "link-btn", onclick: () => pairPhone(token, state) }, w.setup.newCode));
}
