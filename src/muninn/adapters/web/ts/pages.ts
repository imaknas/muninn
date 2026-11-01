// What the panel opens: an application packet or a reply draft, a mail, a reflection, and the settings.

import { api, backUp } from "./api.js";
import { $, el, ICON, kids, must, section, sectionHead, svg, toast, valueOf, type Child } from "./dom.js";
import { fmtMD, fmtTime, MAIL_LABEL, PROVIDER, sender } from "./format.js";
import { afterTalk, confirmationCard, connectAccount, copy } from "./cards.js";
import { cloudFirst, cloudName, handleError, loadToday, reader, render, state } from "./shell.js";
import { setTopic } from "./talk.js";
import { LANGUAGE_NAMES, LANGUAGES, w, type Lang } from "./words.js";
import type { ActivitySetup, Confirmation, Connection, HealthSetup, List, LocationSetup, Mail, Me, Prepared, Reflection, Requests, Settings, SetupPair, SetupSaved, TrackerRow, Undoable } from "./types.js";

// ── Application packet, reply draft ──────────────────────────────────────

export async function renderPacket(id: string): Promise<Child[]> {
  const p = await api<Prepared>(`/api/prepared/${encodeURIComponent(id)}`);
  if (p.kind === "mail_reply") return renderReply(p);
  setTopic({ kind: "packet", id: p.id }, p.title);  // what is said while it is open is about this packet
  const k = p.packet || {};
  const out: Child[] = [
    el("header", { class: "page-head" }, el("div", { class: "stack" },
      el("span", { class: "kicker" }, p.deadline ? w.pages.packetDeadline(w.pages.deadlineName(p.deadline.title), p.deadline.days_left) : w.pages.packet),
      el("h1", { class: "h-md" }, p.title),
      el("span", { class: "provenance" }, el("span", { class: "dot blue" }),
        w.pages.packetPreparedBy(p.prepared_by_label || "Muninn")))),
  ];
  if (k.summary) out.push(el("p", { class: "lead" }, k.summary));
  out.push(afterTalk(p.talk, { kind: "packet", id: p.id }, p.title));
  if (p.form) out.push(...formPart(p));
  if (k.match && k.match.length) out.push(section(w.pages.whyFit, sectionHead(w.pages.whyFit), el("ul", { class: "plain" }, k.match.map((m) => el("li", {}, m)))));
  if (k.gaps && k.gaps.length) out.push(section(w.pages.gaps, sectionHead(w.pages.gapsHead), el("ul", { class: "plain" }, k.gaps.map((m) => el("li", {}, m)))));
  if (k.resume) {
    out.push(el("div", { class: "device" },
      el("div", { class: "body" }, el("span", {}, w.pages.resume(k.resume)), k.resume_focus ? el("span", { class: "meta" }, k.resume_focus) : null),
      /\.pdf$/i.test(k.resume)
        ? el("a", { class: "btn small", href: `/api/documents/${encodeURIComponent(k.resume)}`, download: k.resume }, w.pages.download)
        : el("span", { class: "meta" }, w.pages.exportPdf)));
  }
  if (k.answers && k.answers.length) {
    out.push(section(w.pages.questions, sectionHead(w.pages.questionsHead, w.pages.questionsAside),
      ...k.answers.map((a) => el("div", { class: "answer" },
        el("div", { class: "card-top" }, el("span", {}, a.q),
          el("button", { type: "button", class: "btn small", onclick: () => copy(a.a, a.q) }, w.pages.copy)),
        el("p", {}, a.a)))));
  }
  if (k.ask_first && k.ask_first.length) {
    out.push(section(w.pages.askFirst, sectionHead(w.pages.askFirst),
      el("ul", { class: "checklist" }, k.ask_first.map((q) => el("li", {}, el("span", { class: "box", "aria-hidden": "true" }), el("span", {}, q))))));
  }
  const sendable = p.status === "ready" && (p.form?.state === "filled" || p.form?.state === "held");
  if (p.status === "ready") {
    out.push(el("div", { class: "actions" },
      sendable ? el("button", { type: "button", class: "btn primary block", onclick: () => packetSend(p) }, w.pages.sendForm) : null,
      p.url ? el("a", { class: sendable ? "btn block" : "btn primary block", href: p.url, target: "_blank", rel: "noopener" }, w.pages.openApplication, svg(ICON.out)) : null,
      el("button", { type: "button", class: "btn block", onclick: () => packetDone(p) }, w.pages.sent),
      el("button", { type: "button", class: "link-btn", onclick: () => packetDismiss(p) }, w.pages.skipJob)));
  } else {
    out.push(el("p", { class: "muted" }, p.status === "done" ? w.pages.sentDone : w.pages.skipped));
  }
  return out;
}

// the application form Muninn fills: what is still asked (answered by talking), what each field was filled with and a
// picture of the form filled, or why it stopped; sending it is the page's first button
function formPart(p: Prepared): Child[] {
  const f = p.form!;
  if (f.state === "unsupported") return [el("p", { class: "note" }, w.pages.formUnsupported)];
  if (f.state === "unread") return [];
  if (f.state === "gone") return [el("p", { class: "note" }, w.pages.formGone)];
  if (f.state === "sent") return [el("p", { class: "note" }, w.pages.formSent(f.said || ""))];
  if (f.state === "unsure") return [el("p", { class: "note" }, w.pages.formUnsure)];
  const fields = f.fields || [];
  const asked = fields.filter((x) => x.ask);
  const filled = fields.filter((x) => x.value !== undefined || x.file);
  const shown = (v: string | string[] | undefined, file?: string) => file ? w.pages.formFile(file) : Array.isArray(v) ? v.join(", ") : v || "";
  return [section(w.pages.form, sectionHead(w.pages.formHead(f.site || ""), f.state === "asking" ? w.pages.formAsking : w.pages.formLook),
    f.state === "held" && f.blocked ? el("p", { class: "note" }, w.pages.formHeld) : null,
    asked.length ? el("ul", { class: "checklist" }, asked.map((x) => el("li", {}, el("span", { class: "box", "aria-hidden": "true" }), el("span", {}, x.ask!)))) : null,
    f.shot ? el("a", { class: "form-shot", href: `/api/uploads/${f.shot}` }, el("img", { src: `/api/uploads/${f.shot}`, alt: w.pages.formPicture })) : null,
    filled.length ? el("dl", { class: "form-values" }, filled.map((x) => [el("dt", {}, x.label), el("dd", {}, shown(x.value, x.file))])) : null)];
}

async function packetSend(p: Prepared): Promise<void> {
  const r = await api<{ ok: boolean; text: string }>(`/api/prepared/${p.id}/send`, {});
  toast(r.text);
  render();
}

function renderReply(p: Prepared): Child[] {
  const out: Child[] = [
    el("header", { class: "page-head" }, el("div", { class: "stack" },
      el("span", { class: "kicker" }, w.pages.replyDraft(p.from || "")),
      el("h1", { class: "h-md" }, p.title),
      el("span", { class: "provenance" }, el("span", { class: "dot blue" }), w.pages.replyPreparedBy(p.prepared_by_label || "Muninn")))),
  ];
  if (p.summary) out.push(el("p", { class: "lead" }, p.summary));
  if (p.action) out.push(el("p", { class: "note" }, w.pages.suggestion(p.action)));
  out.push(section(w.pages.draft, el("label", { class: "label", for: "draft" }, w.pages.draftLabel),
    el("textarea", { id: "draft", rows: "10" }, p.draft || "")));
  const draft = () => must<HTMLTextAreaElement>("#draft").value;
  if (p.status === "ready") {
    out.push(el("div", { class: "actions" },
      p.can_save_draft === false
        ? el("button", { type: "button", class: "btn primary block", onclick: () => copy(draft(), w.pages.draft) }, w.pages.copyDraft)
        : el("button", { type: "button", class: "btn primary block", onclick: async () => {
            const r = await api<{ ok: boolean }>(`/api/prepared/${p.id}/draft`, { body: draft() });
            if (r.ok) { toast(w.pages.draftSaved); location.hash = ""; }
          } }, w.pages.saveDraft),
      p.url ? el("a", { class: "btn block", href: p.url, target: "_blank", rel: "noopener" }, w.pages.openOriginal, svg(ICON.out)) : null,
      el("button", { type: "button", class: "link-btn", onclick: () => packetDismiss(p) }, w.pages.noReply)));
  }
  return out;
}

// the same actions the conversation's packet_sent / dismiss_packet take, with the same undo
async function packetDone(p: Prepared): Promise<void> {
  undone(await api<Undoable>(`/api/prepared/${p.id}/done`, {}));
}

async function packetDismiss(p: Prepared): Promise<void> {
  undone(await api<Undoable>(`/api/prepared/${p.id}/dismiss`, {}));
}

function undone(r: Undoable): void {
  toast(r.text);
  location.hash = "";
}

// ── Mail, reflection ────────────────────────────────────────────────────

// `key`: "<account>:<id>"
export async function renderMail(key: string): Promise<Child[]> {
  const i = key.indexOf(":");
  const m = await api<Mail>(`/api/mail/${encodeURIComponent(key.slice(0, i))}/${encodeURIComponent(key.slice(i + 1))}`);
  const out: Child[] = [
    el("header", { class: "page-head" }, el("div", { class: "stack" },
      el("span", { class: "kicker" }, `${sender(m.from)} · ${fmtMD(m.ts)} ${fmtTime(m.ts)}`),
      el("h1", { class: "h-md" }, m.subject || w.pages.noSubject))),
  ];
  if (m.summary) {
    out.push(el("div", { class: "card quiet" },
      el("div", { class: "card-top" }, el("span", {}, w.pages.seenByMuninn), m.category ? el("span", { class: "tag outline" }, MAIL_LABEL[m.category] || m.category) : null),
      el("p", { class: "sub" }, m.summary),
      m.action ? el("p", { class: "note" }, w.pages.nextStep(m.action)) : null));
  }
  out.push(el("div", { class: "mail-body" }, m.text || w.pages.noText));
  out.push(el("div", { class: "actions" },
    m.kind === "work"
      ? el("a", { class: "btn primary block", href: "ms-outlook://" }, w.pages.openOutlook)
      : el("a", { class: "btn primary block", href: "googlegmail://" }, w.pages.openGmail),
    m.web_url ? el("a", { class: "btn block", href: m.web_url, target: "_blank", rel: "noopener" }, w.pages.openInBrowser, svg(ICON.out)) : null));
  return out;
}

export async function renderReflection(id: string | number): Promise<Child[]> {
  const r = await api<Reflection>(`/api/items/${encodeURIComponent(String(id))}`);
  const agg = (r.data && r.data.aggregate) || {};
  const out: Child[] = [
    el("header", { class: "page-head" }, el("div", { class: "stack" },
      el("span", { class: "kicker" }, w.pages.reflectionKicker(agg.sessions || 0, reader())),
      el("h1", { class: "h-md" }, r.title))),
    el("div", { class: "reflection-body" }, r.body),
  ];
  if (agg.themes && agg.themes.length) {
    out.push(section(w.pages.themes, sectionHead(w.pages.themesHead),
      el("div", { class: "chips-line" }, agg.themes.map(([t, n]) => el("span", { class: "chip" }, `${t} ${n}`)))));
  }
  if (agg.repeated_commitments && agg.repeated_commitments.length) {
    out.push(section(w.pages.repeated, sectionHead(w.pages.repeatedHead),
      el("ul", { class: "plain" }, agg.repeated_commitments.map((c) => el("li", {}, c)))));
  }
  if (agg.observed_hours_by_domain) {
    const rows = Object.entries(agg.observed_hours_by_domain).sort((a, b) => b[1] - a[1]);
    out.push(section(w.pages.timeSpent, sectionHead(w.pages.timeSpent, w.pages.timeSpentAside),
      el("div", {}, rows.map(([d, h]) => el("div", { class: "rule" }, el("span", { class: "title" }, d), el("span", { class: "value num" }, `${h}h`))))));
  }
  return out;
}

// ── Progress actions ────────────────────────────────────────────────────

export async function setTrackerStatus(domain: string, row: TrackerRow, status: string): Promise<void> {
  const r = await api<{ change: number }>("/api/tracker/status", { domain, target: row.target, status });
  toast(`${row.title} → ${status}`, { label: w.pages.undo, run: async () => { await api(`/api/tracker/undo/${r.change}`, {}); toast(w.pages.undone); render(); } });
  render();
}

export async function markDone(domain: string, id: string): Promise<void> {
  await api("/api/deadlines/done", { id: `${domain}/${id}`, done: true });
  toast(w.pages.markedDone, { label: w.pages.undo, run: async () => { await api("/api/deadlines/done", { id: `${domain}/${id}`, done: false }); render(); } });
  render();
}

// ── Settings ────────────────────────────────────────────────────────────

// `questions`: the open questions on top (the desktop asks them in its inbox instead)
export async function renderSettings({ questions = true }: { questions?: boolean } = {}): Promise<Child[]> {
  const [t, conf, decided, me, settings, sub] = await Promise.all([
    state.today ? Promise.resolve(state.today) : loadToday(),
    api<List<Confirmation>>("/api/confirmations"), api<List<{ repo: string; domain: string | null }>>("/api/attribution"), api<Me>("/api/me"),
    api<Settings>("/api/settings"), pushSubscribed(),
  ]);
  const titleOf = (id: string) => (t.domain_choices.find((x) => x.id === id) || { title: id }).title;
  const out: Child[] = [];

  if (questions) out.push(...conf.items.map(confirmationCard));

  out.push(section(w.pages.note, el("label", { class: "label", for: "note" }, w.pages.note),
    el("textarea", { id: "note", rows: "3", placeholder: w.pages.notePlaceholder }),
    el("div", { class: "chips", role: "group", "aria-label": w.pages.noteDomain },
      [{ id: "", title: w.pages.noDomain }, ...t.domain_choices].map((d) => el("button", {
        type: "button", "aria-pressed": String(state.noteDomain === d.id),
        onclick: (e) => {
          state.noteDomain = d.id;
          const b = e.target as HTMLElement;
          b.parentNode?.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        },
      }, d.title))),
    el("p", { class: "note" }, cloudFirst() ? w.pages.noteCloud(cloudName()) : w.pages.noteLocal),
    el("button", { type: "button", class: "btn primary block", onclick: saveNote }, w.pages.saveNote)));

  const hourGoals = t.domains.flatMap((d) => d.hour_goals.map((g) => ({ ...g, dtitle: d.title })));
  if (hourGoals.length) {
    if (!hourGoals.some((x) => x.id === state.hoursGoal)) state.hoursGoal = hourGoals[0]?.id ?? null;
    out.push(el("section", { class: "section divider", "aria-label": w.pages.hours, id: "hours" }, hoursBody(hourGoals)));
  }

  const conns = await api<Record<string, Connection>>("/api/connections");
  const purpose: Record<string, string> = { google: w.pages.googlePurpose, microsoft: w.pages.microsoftPurpose(cloudFirst()) };
  for (const [name, c] of Object.entries(conns)) {
    if (!c.configured && !c.authorized) continue;
    const label = PROVIDER[name] || name;
    out.push(el("section", { class: "card", "aria-label": label },
      el("div", { class: "status" },
        el("span", { class: `icon ${c.authorized ? "" : "warn"}` }, svg(c.authorized ? ICON.check : ICON.warn)),
        el("div", {}, el("b", {}, c.authorized ? w.pages.linked(label) : c.status === "relink" ? w.pages.expired(label) : w.pages.notLinked(label)),
          el("span", {}, purpose[name] || ""))),
      el("button", { type: "button", class: c.authorized ? "btn block" : "btn primary block", onclick: () => connectAccount(name) },
        c.authorized ? w.pages.relink : w.pages.link(label))));
  }

  const copyRow = (label: string, shown: string, value: string, what: string) => el("div", { class: "decided-row" }, el("span", {}, label),
    el("span", { class: "acts" }, el("span", { class: "mono muted" }, shown), el("button", { type: "button", class: "btn small", onclick: () => copy(value, what) }, w.pages.copy)));

  const loc = await api<LocationSetup>("/api/setup/location");
  const from = loc.apps.find((a) => a.name === loc.last_app);
  const shown = (f: { value: string; secret: boolean }) => f.secret ? "••••••" : f.value.replace(/^https:\/\//, "").slice(0, 28) + (f.value.length > 36 ? "…" : "");
  out.push(el("section", { class: "card", "aria-label": w.pages.location },
    el("div", { class: "status" },
      el("span", { class: `icon ${loc.last_received ? "" : "warn"}` }, svg(loc.last_received ? ICON.check : ICON.warn)),
      el("div", {}, el("b", {}, loc.last_received ? w.pages.locationOn : w.pages.locationOff),
        el("span", {}, loc.last_received ? w.pages.lastAt(`${fmtMD(loc.last_received)} ${fmtTime(loc.last_received)}`) + (from ? w.pages.via(from.label) : "")
          : w.pages.locationHint))),
    ...loc.apps.map((a) => el("div", { class: "tracker" },
      a.link ? el("a", { class: loc.last_received ? "btn block" : "btn primary block", href: a.link }, w.pages.setUp(a.label)) : null,
      el("details", { class: "fold", ...(a.link ? {} : { open: "" }) }, el("summary", {}, a.link ? w.pages.manualFallback : w.pages.manual(a.label)),
        a.note ? el("p", { class: "note" }, a.note) : null,
        a.fields.map((f) => copyRow(f.label, shown(f), f.value, f.label)))))));

  const act = await api<ActivitySetup>("/api/setup/activity");
  out.push(el("section", { class: "card", "aria-label": w.pages.activity },
    el("div", { class: "status" },
      el("span", { class: `icon ${act.last_received ? "" : "warn"}` }, svg(act.last_received ? ICON.check : ICON.warn)),
      el("div", {}, el("b", {}, act.last_received ? w.pages.activityOn : w.pages.activityOff),
        el("span", {}, act.last_received ? w.pages.lastAt(`${fmtMD(act.last_received)} ${fmtTime(act.last_received)}`) + (act.last_source ? w.pages.via(act.last_source) : "")
          : w.pages.activityHint))),
    ...act.collectors.map((c) => el("div", { class: "tracker" },
      copyRow(c.label, "curl … | bash", c.command, w.pages.installCommand(c.label)),
      el("p", { class: "note" }, c.note))),
    copyRow(w.pages.token, "••••••", act.token, w.pages.token)));

  const hs = await api<HealthSetup>("/api/setup/health");
  const got = hs.last.steps || hs.last.sleep;
  const lastLine = [hs.last.steps ? w.pages.lastSteps(hs.last.steps.steps, fmtMD(hs.last.steps.at)) : null,
    hs.last.sleep ? w.pages.lastSleep(hs.last.sleep.hours, fmtMD(hs.last.sleep.at)) : null].filter(Boolean).join(" · ");
  const steps = (lines: string[]) => el("ol", { class: "plain steps" }, lines.map((l) => el("li", {}, l)));
  out.push(el("section", { class: "card", "aria-label": w.pages.health },
    el("div", { class: "status" },
      el("span", { class: `icon ${got ? "" : "warn"}` }, svg(got ? ICON.check : ICON.warn)),
      el("div", {}, el("b", {}, got ? w.pages.healthOn : w.pages.healthOff),
        el("span", {}, got ? lastLine : w.pages.healthHint))),
    copyRow(w.pages.url, hs.url.replace(/^https:\/\//, "").slice(0, 24) + "…", hs.url, w.pages.url),
    copyRow("Authorization", "Bearer ••••••", hs.authorization, "Authorization"),
    el("details", { class: "fold" }, el("summary", {}, w.pages.howTo),
      el("p", { class: "note" }, w.pages.howToIntro),
      el("b", {}, w.pages.stepsHead),
      steps(w.pages.stepsSteps),
      el("b", {}, w.pages.sleepHead),
      steps(w.pages.sleepSteps),
      el("p", { class: "note" }, w.pages.healthCheck)),
    el("details", { class: "fold" }, el("summary", {}, hs.focus_open ? w.pages.focusOpen(hs.focus_open.name) : w.pages.focusSummary),
      el("p", { class: "note" }, w.pages.focusNote),
      copyRow(w.pages.focusUrl, hs.focus_url.replace(/^https:\/\//, "").slice(0, 24) + "…", hs.focus_url, w.pages.focusUrl),
      steps(w.pages.focusSteps))));

  out.push(el("section", { class: "card", "aria-label": w.pages.push },
    el("div", { class: "status" },
      el("span", { class: `icon ${sub ? "" : "warn"}` }, svg(sub ? ICON.check : ICON.warn)),
      el("div", {}, el("b", {}, sub ? w.pages.pushOn : pushHint()),
        el("span", {}, me.last_push_received ? w.pages.lastDelivered(`${fmtMD(me.last_push_received)} ${fmtTime(me.last_push_received)}`) : w.pages.neverPushed))),
    sub ? el("button", { type: "button", class: "btn block", onclick: testPush }, w.pages.testPush)
        : el("button", { type: "button", class: "btn primary block", onclick: () => enablePush().catch(handleError) }, w.pages.enablePush)));

  if (decided.items.length) {
    out.push(section(w.pages.attribution, sectionHead(w.pages.attribution, w.pages.attributionAside),
      el("div", {}, decided.items.map((d) => el("div", { class: "decided-row" },
        el("span", { class: "mono" }, d.repo),
        el("select", {
          class: `picker ${d.domain ? "" : "none"}`, "aria-label": w.pages.repoDomain(d.repo),
          onchange: async (e) => {
            const domain = valueOf(e) || null;
            const r = await api<{ reattributed: number }>("/api/attribution", { repo: d.repo, domain });
            toast(w.pages.reattributed(d.repo, domain ? titleOf(domain) : w.pages.neither, r.reattributed));
            (e.target as HTMLElement).classList.toggle("none", !domain);
          },
        }, el("option", { value: "", selected: !d.domain }, w.pages.neitherSensitive),
          t.domain_choices.map((x) => el("option", { value: x.id, selected: x.id === d.domain }, x.title))))))));
  }

  const reqs = await api<Requests>("/api/requests");
  if (reqs.items.length) {
    out.push(section(w.pages.requests, sectionHead(w.pages.requestsHead, w.pages.requestCount(reqs.items.length)),
      el("ul", { class: "plain" }, reqs.items.map((r) => el("li", {}, r.text))),
      reqs.live_check_off ? el("p", { class: "muted" }, w.pages.liveCheckOff(reqs.live_check_off)) : null));
  }

  const q = settings.tier_quota || {};
  const maxP = settings.interrupt_max_priority || 3;
  out.push(section(w.pages.interrupts, sectionHead(w.pages.interrupts),
    el("div", {}, ([
      [w.pages.quietHours, w.pages.quietDetail, settings.quiet_hours],
      [w.pages.liveDomains, w.pages.liveDetail(maxP + 1), `P1–P${maxP}`],
      [w.pages.quota, w.pages.quotaDetail(q["1"] ?? "-", q["2"] ?? "-", q["3"] ?? "-", q["escalation"] ?? "-"),
        settings.daily_push_cap ? w.pages.atMost(settings.daily_push_cap) : "-"],
      [w.pages.reports, w.pages.reportsDetail, settings.report_times],
    ] as const).map(([title, detail, value]) => el("div", { class: "rule" },
      el("div", { class: "body" }, el("span", { class: "title" }, title), el("span", { class: "detail" }, detail)),
      el("span", { class: "value num" }, value || "-"))))));

  const models = settings.models || [];
  const none = !models.length && !settings.local_model;
  out.push(section(w.pages.models, sectionHead(w.pages.models, none ? w.pages.modelsNone : cloudFirst() ? w.pages.modelsAllCloud : w.pages.modelsSomeCloud),
    el("div", {}, [
      ...(settings.problems || []).map((p) => el("p", { class: "note warn-text" }, p)),
      none ? el("p", { class: "note" }, w.pages.noModel) : null,
      ...models.map((m) => el("div", { class: "rule" },
        el("div", { class: "body" }, el("span", { class: "title" }, m.label),
          el("span", { class: "detail" }, m.configured ? w.pages.inConfig(m.detail) : m.detail)),
        el("span", { class: "value mono" }, m.model))),
      el("div", { class: "rule" },
        el("div", { class: "body" }, el("span", { class: "title" }, models.length ? w.pages.local : w.pages.all),
          el("span", { class: "detail" }, models.length ? w.pages.localDetail : w.pages.noCloud)),
        el("span", { class: "value mono" }, settings.local_model || "-")),
    ])));

  const off = settings.off || [];
  if (off.length) {
    out.push(section(w.pages.off, sectionHead(w.pages.off, w.pages.offCount(off.length)),
      el("div", {}, off.map((o) => el("div", { class: "rule" },
        el("div", { class: "body" }, el("span", { class: "title" }, o.feature), el("span", { class: "detail" }, o.why)))))));
  }

  out.push(languageSection(settings.language));

  out.push(section(w.pages.privacy, sectionHead(w.pages.privacy), el("ul", { class: "plain" },
    el("li", {}, w.pages.pushPrivacy),
    ...(cloudFirst()
      ? [el("li", {}, w.pages.allCloud(cloudName())),
        el("li", {}, w.pages.callsKept)]
      : [el("li", {}, w.pages.sensitiveLocal),
        el("li", {}, w.pages.cloudSees)]))));

  out.push(el("div", { class: "device" },
    el("div", { class: "body" }, el("span", {}, me.device.name),
      el("span", { class: "meta" }, (me.device.created ? w.pages.pairedOn(fmtMD(me.device.created)) : w.pages.paired) + (settings.allowed_logins ? w.pages.tailscaleOnly : ""))),
    el("span", { class: "meta" }, w.pages.thisDevice)));
  out.push(pairSection());
  return out;
}

// another device (the phone): a code and a QR code for it, once Muninn has an HTTPS address; until then what gives it one
function pairSection(): HTMLElement {
  const body = el("div", {});
  const show = async () => {
    try {
      const p = await api<SetupPair>("/api/devices/pair", {});
      body.replaceChildren(
        el("p", { class: "sub" }, w.pages.pairScan),
        el("img", { src: `data:image/svg+xml;base64,${btoa(p.qr)}`, alt: p.url, class: "pair-qr", width: 200, height: 200 }),
        el("p", { class: "note" }, w.pages.pairByHand(p.url.split("?")[0] || p.url, p.code)));
    } catch (err) {
      body.replaceChildren(el("p", { class: "note" }, err instanceof Error ? err.message : String(err)),
        el("p", { class: "note" }, w.pages.pairNeedsHttps(location.port)));
    }
  };
  return section(w.pages.pairTitle, sectionHead(w.pages.pairTitle),
    el("button", { type: "button", class: "btn", onclick: show }, w.pages.pairButton), body);
}

function hoursBody(hourGoals: { id: string; title: string }[]): Child[] {
  const g = hourGoals.find((x) => x.id === state.hoursGoal) || hourGoals[0];
  if (!g) return [];
  const v = state.hoursValue;
  const rerender = () => must("#hours").replaceChildren(...kids(hoursBody(hourGoals)));
  return [
    sectionHead(w.pages.hours, w.pages.hoursAside),
    el("div", { class: "chips", role: "group", "aria-label": w.pages.goal }, hourGoals.map((x) => el("button", {
      type: "button", "aria-pressed": String(x.id === g.id), onclick: () => { state.hoursGoal = x.id; rerender(); },
    }, x.title))),
    el("div", { class: "stepper" },
      el("button", { type: "button", "aria-label": w.pages.minusHalf, onclick: () => { state.hoursValue = v - 0.5; rerender(); } }, "−"),
      el("div", { class: "val" }, el("b", { class: "num" }, `${v > 0 ? "+" : ""}${v.toFixed(1)}`, el("small", {}, " h")),
        el("span", {}, `${g.title} · ${state.showDate ? w.pages.pickedDate : w.pages.today}`)),
      el("button", { type: "button", "aria-label": w.pages.plusHalf, onclick: () => { state.hoursValue = v + 0.5; rerender(); } }, "+")),
    state.showDate ? el("input", { type: "date", id: "hours-date", "aria-label": w.pages.date }) : null,
    el("div", { class: "row-btns" },
      el("button", { type: "button", class: "btn primary", onclick: () => saveHours(g) }, state.showDate ? w.pages.addToThatDay : w.pages.addToToday),
      el("button", { type: "button", class: "btn", onclick: () => { state.showDate = !state.showDate; rerender(); } }, state.showDate ? w.pages.backToToday : w.pages.changeDate)),
  ];
}

async function saveNote(): Promise<void> {
  const note = must<HTMLTextAreaElement>("#note");
  const text = note.value.trim();
  if (!text) return toast(w.pages.writeSomething);
  await api("/api/input/note", { text, domain: state.noteDomain || null });
  note.value = "";
  toast(w.pages.saved);
}

async function saveHours(g: { id: string; title: string }): Promise<void> {
  const picked = $<HTMLInputElement>("#hours-date");
  const date = state.showDate && picked ? picked.value || null : null;
  if (!state.hoursValue) return toast(w.pages.zeroHours);
  await api("/api/input/hours", { goal: g.id, hours: state.hoursValue, date, note: "" });
  toast(`${g.title} ${state.hoursValue > 0 ? "+" : ""}${state.hoursValue}h`);
  state.hoursValue = 1;
  state.showDate = false;
  state.today = null;
  render();
}

// ── push ────────────────────────────────────────────────────────────────

export function standalone(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

function pushHint(): string {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return standalone() ? w.pages.pushUnsupported : w.pages.addToHomeScreen;
  if (Notification.permission === "denied") return w.pages.pushDenied;
  return w.pages.pushOff;
}

async function pushSubscribed(): Promise<boolean> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  const reg = await navigator.serviceWorker.ready;
  return !!(await reg.pushManager.getSubscription());
}

function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function enablePush(): Promise<void> {
  const perm = await Notification.requestPermission(); // must run inside the tap handler on iOS
  if (perm !== "granted") return toast(w.pages.noPermission);
  const { key } = await api<{ key: string }>("/api/push/key");
  const reg = await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription()) ||
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) }));
  await api("/api/push/subscribe", { subscription: sub.toJSON() });
  toast(w.pages.pushOn);
  render();
}

async function testPush(): Promise<void> {
  const r = await api<{ ok: boolean }>("/api/push/test", {}).catch(handleError);
  if (r) toast(r.ok ? w.pages.testSent : w.pages.testFailed);
}

// what Muninn speaks: changed in config, Muninn starts again in it and the page opens again
function languageSection(current: string | undefined): HTMLElement {
  return section(w.language.title, sectionHead(w.language.title),
    el("div", { class: "chips", role: "group", "aria-label": w.language.title },
      (Object.keys(LANGUAGES) as Lang[]).map((code) => el("button", {
        type: "button", "aria-pressed": String(code === current),
        onclick: async () => {
          if (code === current) return;
          try {
            const r = await api<SetupSaved>("/api/settings/language", { language: code });
            if (!r.restarting) { toast(w.language.restartYourself); return; }
            toast(w.language.restarting);
            await backUp();
            location.reload();
          } catch (err) {
            handleError(err);
          }
        },
      }, LANGUAGE_NAMES[code]))),
    el("p", { class: "note" }, w.language.detail));
}
