// The server's JSON API. A 401 means this device is not paired (or no longer is).

export class Unpaired extends Error {}

let doing = 0;  // what the user asked for (a sentence, a button) still on its way
// something the user did is still on its way: nothing redraws under it meanwhile
export const busy = (): boolean => doing > 0;

// the page is being left (a reload, another address): a read it cuts off is not an error, and nothing waits on it
let leaving = false;
addEventListener("beforeunload", () => { leaving = true; });
addEventListener("pagehide", () => { leaving = true; });
addEventListener("pageshow", () => { leaving = false; });  // back again from the browser's cache

export async function api<T>(path: string, body?: unknown): Promise<T> {
  // nothing is asked once the page is being left: the browser would refuse it, and say so as an error
  if (leaving && body === undefined) return new Promise<T>(() => {});
  const opts: RequestInit = { credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
    doing++;
  }
  try {
    let r: Response;
    try {
      r = await fetch(path, opts);
    } catch (e) {
      if (leaving) return new Promise<T>(() => {});  // cut off by leaving the page: never settles, never said
      throw e;
    }
    if (r.status === 401) throw new Unpaired();
    const data = (await r.json().catch(() => ({}))) as T & { error?: string };
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  } finally {
    if (body !== undefined) doing--;
  }
}

// Muninn started again (after a setting it reads at start changed): resolves once it answers, or after a minute
export async function backUp(): Promise<void> {
  await new Promise((r) => setTimeout(r, 1500));
  for (let i = 0; i < 60; i++) {
    try {
      if ((await fetch("/healthz", { cache: "no-store" })).ok) return;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
}
