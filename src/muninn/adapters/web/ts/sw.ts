// The service worker (a classic script, compiled on its own: tsconfig.sw.json). The push payload only says
// "<domain>: N new reminders"; content is fetched from the server when the app opens. API responses are never cached.

const sw = self as unknown as ServiceWorkerGlobalScope;

// the page's language, from the address the page registered this worker at (/sw.js?lang=<code>)
const LANG = new URL(location.href).searchParams.get("lang") === "en" ? "en" : "zh-TW";
// the fallback a push shows when it carries no text of its own, in each language the page speaks
const NEW_REMINDERS: Record<string, string> = { "zh-TW": "有新的提醒", en: "New reminders" };
// every screen's words module (ts/words/<lang>/<area>.ts; tests/test_languages.py keeps this list whole)
const WORD_AREAS = ["app", "cards", "files", "format", "language", "pages", "panel", "setup", "shell", "stamps", "talk", "things", "viewer"];

const SHELL = "muninn-shell-v41";
const SHELL_FILES = ["/", "/static/style.css", "/static/icon-192.png", "/manifest.webmanifest",
  ...["app", "api", "cards", "dom", "files", "format", "pages", "panel", "setup", "shell", "stamps", "talk", "things", "viewer",
    "words", "words/zh-TW", "words/en", ...WORD_AREAS.map((a) => `words/${LANG}/${a}`)].map((m) => `/static/${m}.js`)];

sw.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => sw.skipWaiting()));
});

sw.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => sw.clients.claim()),
  );
});

sw.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // network first, so a deploy shows up immediately; shell from cache when offline
  event.respondWith(
    fetch(event.request, { cache: "no-cache" })  // always revalidate: a deploy shows up on the next open
      .then((r) => {
        if (r.ok) caches.open(SHELL).then((c) => c.put(event.request, r.clone()));
        return r;
      })
      .catch(async () => (await caches.match(event.request)) || (await caches.match("/")) || Response.error()),
  );
});

sw.addEventListener("push", (event) => {
  let msg = { title: "Muninn", body: NEW_REMINDERS[LANG] ?? "" };
  try {
    msg = { ...msg, ...(event.data ? (event.data.json() as Partial<typeof msg>) : {}) };
  } catch (e) { /* not JSON: the default text */ }
  // Tell the server this device received the push (diagnostics only; a
  // failure here must not stop the notification from showing).
  const ack = fetch("/api/push/ack", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stage: "received" }),
  }).catch(() => {});
  const options: NotificationOptions & { renotify?: boolean } = {
    body: msg.body,
    tag: "muninn",
    renotify: true,
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: { url: "/" },  // the conversation, where Muninn says what it is about
  };
  event.waitUntil(Promise.all([ack, sw.registration.showNotification(msg.title, options)]));
});

sw.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data as { url?: string } | null;
  const target = (data && data.url) || "/";
  event.waitUntil(
    sw.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ("focus" in w) {
          w.navigate(target).catch(() => {});
          return w.focus();
        }
      }
      return sw.clients.openWindow(target);
    }),
  );
});
