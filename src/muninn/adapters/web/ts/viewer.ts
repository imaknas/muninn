// A picture or file opened from the conversation, shown over it with a way back. The installed app has no tabs and
// no back button: a link opened "in a new tab" there takes over the whole screen with nothing to close it.

import { el } from "./dom.js";
import { standalone } from "./pages.js";
import { w } from "./words.js";

const OPENS = "a.sent, a.shown-file, a.form-shot";  // the photos and files the user sent, the files a session showed him, a filled form

export function mountViewer(): void {
  document.addEventListener("click", (ev) => {
    const a = (ev.target as HTMLElement).closest<HTMLAnchorElement>(OPENS);
    if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
    const img = a.querySelector("img");
    if (!img && !standalone()) return;  // a file in a browser: its own tab, which closes like any other
    ev.preventDefault();
    open(a.href, img ? img.alt : (a.textContent || "").replace(w.viewer.openPrefix, ""), !!img);
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") close(); });
}

function open(url: string, name: string, picture: boolean): void {
  close();
  const shut = el("button", { type: "button", class: "btn small", onclick: close }, w.viewer.close);
  const body = picture
    ? el("img", { src: url, alt: name, onclick: close })
    : el("iframe", { src: url, title: name });
  const box = el("div", { id: "viewer", role: "dialog", "aria-modal": "true", "aria-label": name },
    el("div", { class: "viewer-bar" }, el("span", { class: "viewer-name" }, name), shut),
    el("div", { class: "viewer-body", onclick: (ev: Event) => { if (ev.target === ev.currentTarget) close(); } }, body));
  document.body.append(box);
  shut.focus();
}

function close(): void {
  document.getElementById("viewer")?.remove();
}
