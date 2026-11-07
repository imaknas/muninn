// The entry point. The screen is the conversation, its input, one button at the top saying how many things wait,
// and the panel that button opens; it is the same at every width (the stylesheet lays it out). Redraws when
// the address or the app's visibility changes, and after the user does something.

import { api, busy } from "./api.js";
import { el, kids, must } from "./dom.js";
import { startCard } from "./cards.js";
import { renderPanel, route } from "./panel.js";
import { keepPushing, standalone } from "./pages.js";
import { renderSetup } from "./setup.js";
import { connect, handleError, loadSettings, reportError } from "./shell.js";
import { closeTalk, fit, loadStream, markStale, mountTalk, refreshStream, setTopic, talking, watchOpen } from "./talk.js";
import { loadData, summary, things, type Data, type Thing } from "./things.js";
import { mountViewer } from "./viewer.js";
import { lang, w } from "./words.js";

const NARROW = matchMedia("(max-width: 900px)");

// the page's fixed parts, in the page's language, before the page shows (index.html carries the Chinese)
must("#stream").setAttribute("aria-label", w.app.conversation);
must("label[for=say-input]").textContent = w.app.sayLabel;
must<HTMLTextAreaElement>("#say-input").placeholder = w.app.sayPlaceholder;
must("#say button[type=submit]").setAttribute("aria-label", w.app.send);
must("#panel").setAttribute("aria-label", w.app.panel);

let seq = 0;  // a slower, older read must not overwrite a newer one
let started = false;
let first = false;
let away = false;  // the app was out of view: the stream is checked against the server when it comes back
let returned = false;  // the user brought the app back into view (not only something changing meanwhile)
// what waits, as last read: the panel and the button draw from it at once; the server says when it changed
let held: { data: Data; list: Thing[]; at: number } | null = null;
const STALE_MS = 5 * 60_000;  // read again in the background when the panel opens on something older than this

// Opening, closing or moving about the panel is the screen's own: drawn at once from what is held, never waiting
// on the network. `fresh`: what is held was just read again (a view that reads its own data reads it again too).
function show(fresh = false): void {
  const r = route();
  const panel = must("#panel");
  const opening = !!r.view && panel.hidden;
  panel.hidden = !r.view;
  must("#scrim").hidden = !r.view || !NARROW.matches;
  must("#pill").setAttribute("aria-expanded", String(!!r.view));
  document.body.classList.toggle("panel-open", !!r.view);
  if (!r.view) { delete panel.dataset.key; setTopic(null, null); return; }  // closed: no longer what the next sentence is about
  if (fresh && panel.contains(document.activeElement) && document.activeElement !== panel) return;  // typing in it
  if (opening) { panel.classList.remove("opening"); void panel.offsetWidth; panel.classList.add("opening"); }
  renderPanel(panel, r, held?.data ?? null, held?.list ?? [], fresh);
}

// Reads again what the screen shows (the conversation as it goes on, what waits) and draws it without a sound.
async function render(): Promise<void> {
  const mine = ++seq;
  try {
    if (!started) {
      started = true;
      must("#app").hidden = false;
      must("#pair").hidden = true;
      await loadSettings();
      await opened();
      await loadStream();
      first = true;
      listen();
    } else {
      const back = away;
      away = false;
      if (returned) { returned = false; await opened(); }
      // what waits first: it is what the user just acted on; the conversation meanwhile
      const waited = readWaiting(mine).catch((e) => { handleError(e); return false; });
      await refreshStream(back);  // what Muninn brought up meanwhile, and what was said on another device
      await waited;
      return;
    }
    if (!(await readWaiting(mine))) return;
    if (first) {  // opened: the latest of the conversation, and what waits, in view
      first = false;
      const stream = must("#stream");
      stream.scrollTop = stream.scrollHeight;
    }
  } catch (e) {
    handleError(e);
  }
}

// The app is in view: after a while away Muninn opens with what waits, said before the conversation is read again.
// Nothing fails for want of it, and it is not something the user did (a change meanwhile is drawn as usual).
async function opened(): Promise<void> {
  await fetch("/api/opened", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}" })
    .catch(() => undefined);
}

// Reads again what waits, and draws the button and the panel from it (never the conversation): what changed
// elsewhere shows there even while the user is in the middle of something in the conversation. False when a newer
// read has started meanwhile.
async function readWaiting(mine = ++seq): Promise<boolean> {
  const data = await loadData();
  const list = things(data);
  if (mine !== seq) return false;
  held = { data, list, at: Date.now() };
  drawPill(data, list);
  drawStart(data.home.domains);
  show(true);
  return true;
}

function drawPill(data: Parameters<typeof summary>[0], list: Thing[]): void {
  const { waiting, today } = summary(data, list);
  const pill = must("#pill");
  pill.setAttribute("aria-label", w.app.pillLabel(waiting, today));
  pill.replaceChildren(...kids([
    waiting ? el("span", { class: "dot", "aria-hidden": "true" }) : null,
    waiting ? el("b", {}, w.app.pillWaiting(waiting)) : null,
    el("span", { class: waiting ? "muted" : "" }, `${waiting ? "· " : ""}${w.app.pillToday(today)}`),
    el("span", { class: "chev", "aria-hidden": "true" }),
  ]));
}

// the conversation is only the conversation: what waits on the user is in the panel, not here. A new user with no
// domain yet gets one thing at the end of it: where to start.
function drawStart(domains: number): void {
  must("#waiting").replaceChildren(...kids([domains ? null : startCard()]));
}

// pairing: the first thing an unpaired device sees
function renderPair(error?: string): void {
  must("#app").hidden = true;
  const pair = must("#pair");
  pair.hidden = false;
  pair.replaceChildren(...kids([
    el("h1", {}, w.app.pairTitle),
    el("p", { class: "sub" }, w.app.pairHow),
    standalone() ? null : el("p", { class: "note" }, w.app.pairHomeScreen),
    el("form", { class: "pair-form", onsubmit: async (e) => {
      e.preventDefault();
      const f = new FormData(e.target as HTMLFormElement);
      try {
        await api("/api/pair", { code: f.get("code"), name: f.get("name") || (standalone() ? "iPhone" : "browser") });
        location.hash = "#settings";
        location.reload();
      } catch (err) {
        renderPair(err instanceof Error ? err.message : String(err));
      }
    } },
      el("input", { name: "code", placeholder: "XXXX-XXXX", autocomplete: "one-time-code", autocapitalize: "characters", required: true, "aria-label": w.app.pairCode,
        value: new URLSearchParams(location.search).get("pair") || "" }),  // the code the setup page's QR carries
      el("input", { name: "name", placeholder: w.app.deviceNamePlaceholder, "aria-label": w.app.deviceName }),
      el("button", { type: "submit", class: "btn primary block" }, w.app.pair)),
    error ? el("p", { class: "error" }, error) : null]));
}

connect({
  render,
  settled(thing) {
    if (held) {
      held = { ...held, list: held.list.filter((x) => x.id !== thing) };
      drawPill(held.data, held.list);
    }
    const r = route();
    if (r.view === "item" && r.param === thing) location.hash = "#things";  // its page has nothing left to show
    else show(true);
    readWaiting().catch(handleError);  // then as the server has it (the conversation is not read again for this)
  },
  prompt(text) {
    const input = must<HTMLTextAreaElement>("#say-input");
    location.hash = NARROW.matches ? "" : location.hash;  // a phone's panel covers the input
    input.value = text;
    fit(input);
    input.focus();
  },
  unpaired: () => { closeTalk(); started = false; if (changes) { changes.close(); changes = null; } renderPair(); },
});

// a phone opens on the conversation: the addresses a launch from the home screen or a notification used to carry
// (an installed app keeps the one it was added with) do not open the panel over it
if (NARROW.matches && ["#today", "#things"].includes(location.hash)) history.replaceState(null, "", location.pathname + location.search);
// nothing fails without a word: an error no part of the screen handled is said, and kept in the server's log
window.addEventListener("unhandledrejection", (ev) => {
  const e = ev.reason;
  reportError(e, location.hash || "/");
  handleError(e);
});
window.addEventListener("error", (ev) => reportError(ev.message, `${ev.filename}:${ev.lineno}`));

mountTalk();
mountViewer();
watchOpen(() => { if (NARROW.matches && route().view) location.hash = ""; });  // the conversation, not the panel, on a phone
must("#pill").addEventListener("click", () => { location.hash = route().view ? "" : "#things"; });
must("#scrim").addEventListener("click", () => { location.hash = ""; });
window.addEventListener("hashchange", () => {
  show();
  if (held && Date.now() - held.at > STALE_MS) void render();  // long since read: again, behind what is shown
});
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { away = returned = true; render(); } });
window.addEventListener("focus", () => { away = returned = true; render(); });  // a window left open beside another one

// while in view, one stream says when anything shown changed (another device, Muninn on its own): drawn then. Not
// while something the user did here is on its way: then it is drawn at the next change or when they come back
let changes: EventSource | null = null;
function listen(): void {
  if (document.visibilityState !== "visible") {
    if (changes) { changes.close(); changes = null; }
    return;
  }
  if (changes || !started) return;
  changes = new EventSource("/api/changes/events");
  // (re)connected: whatever changed while it was not listening (since the page was drawn, or a dropped line)
  const changed = (): void => {
    if (busy() || talking()) {  // the conversation later (nothing redrawn under them); what waits, now
      markStale();
      readWaiting().catch(handleError);
      return;
    }
    away = true;
    void render();
  };
  changes.onopen = changed;
  changes.onmessage = changed;
}
document.addEventListener("visibilitychange", listen);
if ("serviceWorker" in navigator) navigator.serviceWorker.register(`/sw.js?lang=${lang}`, { scope: "/" }).catch(() => {});
const setupToken = location.hash.startsWith("#setup=") ? location.hash.slice("#setup=".length) : "";
if (setupToken) void renderSetup(setupToken);  // the first-run link: setting up, not the app yet
else {
  render();
  void keepPushing().catch(() => {});
}
