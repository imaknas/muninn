// The files waiting under the input to go with what the user says next: picked, pasted or dropped, each one
// sent to the server at once (in the background), so pressing send never waits on an upload.

import { Unpaired } from "./api.js";
import { el, must } from "./dom.js";
import { w } from "./words.js";
import type { Sent } from "./types.js";

const TAKEN = ["image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf", "text/plain", "text/markdown", "text/csv"];
const MAX_FILES = 8;
const LONGEST = 2560;  // a photo larger than this is made smaller first: models read no more, and it goes up faster

interface Waiting { name: string; preview: string | null; done: Promise<Sent | null>; chip: HTMLElement }
let waiting: Waiting[] = [];

export function mountFiles(): void {
  const form = must("#say");
  const picker = el("input", { type: "file", multiple: true, accept: "image/*,application/pdf,text/plain,text/markdown,text/csv", hidden: true,
    onchange: (e) => { const i = e.target as HTMLInputElement; add(Array.from(i.files || [])); i.value = ""; } });
  const clip = el("button", { type: "button", class: "clip", "aria-label": w.files.attach, onclick: () => picker.click() });
  clip.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9"></path></svg>';
  form.prepend(el("div", { id: "say-files", hidden: true }), clip, picker);
  must("#say-input").addEventListener("paste", (e) => {
    const files = Array.from((e as ClipboardEvent).clipboardData?.files || []);
    if (files.length) { e.preventDefault(); add(files); }
  });
  const app = must("#app");
  app.addEventListener("dragover", (e) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
  app.addEventListener("drop", (e) => {
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length) { e.preventDefault(); add(files); }
  });
}

export const hasFiles = (): boolean => waiting.length > 0;

// what goes with the sentence being sent: the uploads finished (waits only for any still going), the row emptied
export async function takeFiles(): Promise<{ sent: Sent[]; previews: (string | null)[]; failed: string[] }> {
  const these = waiting;
  waiting = [];
  draw();
  const done = await Promise.all(these.map((item) => item.done));
  const sent: Sent[] = [];
  const previews: (string | null)[] = [];
  const failed: string[] = [];
  these.forEach((item, i) => {
    const s = done[i];
    if (s) { sent.push(s); previews.push(item.preview); } else failed.push(w.files.notSent(item.name, item.chip.title));
  });
  return { sent, previews, failed };
}

// a file the user sent, as the conversation shows it: the photo itself, else its name as a link
export function sentNode(s: Sent, preview?: string | null): HTMLElement {
  const url = `/api/uploads/${encodeURIComponent(s.id)}`;
  return s.media_type.startsWith("image/")
    ? el("a", { class: "sent photo", href: url, target: "_blank", rel: "noopener" }, el("img", { src: preview || url, alt: s.name, loading: "lazy" }))
    : el("a", { class: "sent file", href: url, target: "_blank", rel: "noopener" }, s.name);
}

function add(files: File[]): void {
  for (const f of files) {
    if (waiting.length >= MAX_FILES) { note(w.files.tooMany(MAX_FILES)); break; }
    if (!TAKEN.includes(f.type)) { note(w.files.notTaken(f.name)); continue; }
    const preview = f.type.startsWith("image/") ? URL.createObjectURL(f) : null;
    const chip = el("span", { class: "chip" }, preview ? el("img", { src: preview, alt: "" }) : null, el("span", { class: "name" }, f.name));
    const item: Waiting = { name: f.name, preview, chip, done: Promise.resolve(null) };
    chip.append(el("button", { type: "button", "aria-label": w.files.remove(f.name), onclick: () => { waiting = waiting.filter((x) => x !== item); draw(); } }, "×"));
    chip.classList.add("going");
    item.done = upload(f).then((s) => { chip.classList.remove("going"); return s; }, (err) => {
      chip.classList.remove("going");
      chip.classList.add("failed");
      chip.title = err instanceof Error ? err.message : w.files.failed;
      return null;
    });
    waiting.push(item);
  }
  draw();
}

function draw(): void {
  const row = must("#say-files");
  row.replaceChildren(...waiting.map((item) => item.chip));
  row.hidden = !waiting.length;
}

function note(text: string): void {
  must("#say-files").hidden = false;
  must("#say-files").append(el("span", { class: "chip note" }, text));
}

async function upload(f: File): Promise<Sent> {
  const body = f.type.startsWith("image/") && f.type !== "image/gif" ? await smaller(f) : f;
  const r = await fetch(`/api/uploads?name=${encodeURIComponent(f.name)}`, {
    method: "POST", credentials: "same-origin", body,
    headers: { "Content-Type": body.type || f.type, "X-Muninn-Upload": "1" },
  });
  if (r.status === 401) throw new Unpaired();
  const data = (await r.json().catch(() => ({}))) as Sent & { error?: string };
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

// a photo with a side longer than LONGEST, redrawn at that size as a JPEG; any other image as it is
async function smaller(f: File): Promise<Blob> {
  try {
    const img = await createImageBitmap(f);
    const scale = LONGEST / Math.max(img.width, img.height);
    if (scale >= 1) return f;
    const c = el("canvas", { width: Math.round(img.width * scale), height: Math.round(img.height * scale) });
    c.getContext("2d")?.drawImage(img, 0, 0, c.width, c.height);
    return await new Promise<Blob>((ok) => c.toBlob((b) => ok(b || f), "image/jpeg", 0.9));
  } catch {
    return f;  // the browser cannot read it: sent as it is
  }
}
