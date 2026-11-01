// What the server's API answers, as the pages read it (adapters/web/app.py and the core's view builders).

export interface List<T> { items: T[] }

// a discussion about an item (Engine.suggestions_for): the latest one and the next steps it offered
export interface Talk { thread: string; last: string; suggest: Button[] }
// a next step a conversation offers: pressed by its id (index: its place, from before ids were kept); turn: the line
// Muninn said unasked it goes with
export interface Button { index: number; label: string; id?: string; turn?: string; plain?: boolean }  // plain: none put forward

export interface About { kind: "review" | "question" | "task" | "project_question" | "packet" | "alert" | "onboarding" | "idea"; id: string | number }

export interface Settings {
  routing?: string;
  cloud_label?: string | null;  // what the user calls the cloud model (none: no cloud)
  quiet_hours?: string;
  interrupt_max_priority?: number;
  tier_quota?: Record<string, number>;
  daily_push_cap?: number;
  report_times?: string;
  allowed_logins?: string[] | boolean;
  models?: TaskModel[];  // each kind of work and the cloud model it uses now (core/task_models.py)
  local_model?: string | null;
  problems?: string[];  // model settings that cannot work as written: the commands that ask models refuse until fixed
  off?: { feature: string; why: string }[];  // features this installation does without, each with why and what turns it on
  language?: string;  // what Muninn speaks (config `language`)
}
export interface TaskModel { key: string; label: string; detail: string; model: string; configured: boolean }

export interface Place { start: string; end: string; hours: number; place: string | null; domain: string | null; lat: number; lon: number }
export interface MailRow {
  ts: string; from: string | null; subject: string | null; category: string | null; summary: string | null; action: string | null;
  thread: string | null; account: string; id: string;
}
export interface Ready {
  id: number; kind: string; domain: string; target: string; title: string; summary: string | null; from: string | null; prepared_by: string; prepared_by_label: string; ts: string;
}
export interface Done { id: number; ts: string; domain: string; text: string; undoable: boolean }
export interface Next { text: string; why: string; kind: string; target: string | null; domain: string; domain_title: string; domain_priority: number; priority: number }
// one thing waiting on the user, or being worked on, in the order shown (core/waiting.py); drawn by its kind
export interface Waiting {
  id: string; kind: string; group: "wait" | "doing"; about: About | null; thread: string | null; lease: string | null;
  by: string | null;  // who asks it, as the user calls them
  lines: string[];  // what it is made of, when it is several (a deep talk's undecided questions)
}
export interface Task {
  key: string; n: number; title: string; due: string | null; context: string | null; projects: string[]; talk: Talk | null;
  mail: string | null;  // account:id of the mail it came from
}
// an idea that belongs to no plan yet (core/ideas.py)
export interface Idea { n: number; text: string; ts: string; status: "open" | "shelved"; talk: Talk | null }
export interface Report { id: number; ts: string; kind: string; title: string; body: string }

export interface Home {
  places: Place[];
  relink: string[];
  mail: MailRow[];
  schedule: { id: string; title: string; start: string; end: string }[];
  now: string;
  reflection: { id: number; ts: string; title: string } | null;
  my_project_todos: { project: string; items: string[] }[];
  ready: Ready[];
  done: Done[];
  next: Next[];
  week: { domain: string; title: string; value: string; state: string }[];
  tasks: Task[];
  ideas: Idea[];
  reports: Report[];
  domains: number;  // how many domains there are; 0 for a new user
  pending_confirmations: number;
  unread: number;
}

export interface ConfirmationData {
  why?: string;
  match?: string;
  observations?: number;
  suggestion?: string;
  titles?: Record<string, string>;
  neutral?: boolean;  // no answer is put forward: none of its buttons is the suggested one
  [k: string]: unknown;
}
export interface Confirmation {
  id: number; ts: string; kind: string; question: string; options: string[]; data: ConfirmationData | null; talk: Talk | null;
}

export interface ReviewItem {
  id: string; kind: "goal" | "deadline"; domain: string; domain_title: string; title: string; detail: string;
  value: number | string; actions: string[]; source: string | null; talk?: Talk | null;
}

export interface ProjectQuestion {
  id: string; project: string; text: string; ts?: string; lease_thread: string | null; lease_id: string | null; talk: Talk | null; label?: string;
}

export type StepKind = "ask_user" | "read" | "search" | "write_file" | "run_command" | "open_web" | "other";
export interface PermissionRequest {
  id: string; lease: string; project?: string; label?: string; ts?: string;
  kind: StepKind;  // a question for the user, a web page to open, (work mode) a file to write or a command to run
  url?: string; summary?: string; domain?: string; flagged?: boolean; allow_session?: boolean;
  // multi: several labels may be chosen (answered as a list)
  question?: { questions?: { question: string; multi?: boolean; options?: { label: string; description?: string }[] }[] } | null;
}

export interface ActiveLease { lease_id: string; project: string; label?: string; thread: string; state: string; warn: boolean }
export interface Turn { role: "user" | "answer" | "session" | string; text: string; ts?: string | null; files?: Sent[]; detail?: string }
// a file the user sent with what they said: kept on the server, shown again from /api/uploads/{id}
// (lease, path: a file a deep talk's session showed him, from that talk)
export interface Sent { id: string; name: string; media_type: string; size?: number; lease?: string; path?: string }
export interface Handover {
  project?: string; fallback?: boolean; summary?: string;
  decisions?: { what: string; why?: string }[];
  open_questions?: string[];
  tasks_for_owner?: { text: string; due?: string }[];
}
export type LeaseMode = "discuss" | "work";
export interface LeaseView { lease_id: string; project: string; label?: string; state: string; mode?: LeaseMode; can_work?: boolean; warn?: boolean; turns: Turn[] }
export interface LeaseInfo {
  enabled: boolean;
  active?: ActiveLease[];
  lease?: LeaseView | null;
  attachable?: string[];
  quota_warning?: string | null;
  transcript?: { project: string; label?: string; ended?: string | null; turns: Turn[]; handover: Handover | null } | null;
}

export interface Outcome {
  text: string; action_id?: number | null; link?: string | null; confirm_id?: number | null; options?: string[]; handoff_id?: number | null;
}
// what /api/command answers: an ordinary turn (with outcomes), or, for a slash command or a lent
// conversation, the state of the lease
export interface CommandReply {
  thread?: string;
  reply?: string | null;
  outcomes?: Outcome[];
  suggest?: Button[];  // every one the conversation still offers, its other lines' too
  stood_in?: boolean;  // a local model answered in place of the cloud
  needs_confirm?: string;
  project?: string;
  label?: string;
  lease_id?: string;
  leased?: boolean;
  state?: string;
  mode?: LeaseMode;  // a lease's mode: on attaching, and after switching (with `note`)
  can_work?: boolean;
  note?: string;
  lent?: Lent;  // the round opened an idea's sandbox and the conversation went on there
  topic?: { title: string | null; switched: boolean };  // the conversation it went on with (switched: Muninn judged it was another one)
}
// a conversation just lent (or, when usage is high, the question whether to go on)
export interface LeaseReply {
  needs_confirm?: string; thread?: string | null; lease_id?: string; project?: string; label?: string; state?: string; mode?: LeaseMode; can_work?: boolean;
}
// a conversation lent because the user asked to try an idea out: said in one line, or why it could not be
export interface Lent {
  needs_confirm?: string; thread?: string | null; lease_id?: string; project?: string; label?: string; state?: string; mode?: LeaseMode; can_work?: boolean;
  text?: string; error?: string; opened?: boolean;  // opened: the session was handed what was said, and is answering
}
export interface Undoable { text: string; action_id: number }
// what the user asked of Muninn itself and is not live yet; live_check_off: why nothing is marked live by itself
export interface Requests { items: { id: number | null; text: string; ts: string }[]; live_check_off: string | null }

// a topic of the conversation and where it stands (core/topics.py): waiting on the user, worked on, set aside, closed
export interface TopicState {
  thread: string; about: About | null; title: string; state: "waiting" | "doing" | "open" | "shelved" | "closed"; now: string; ts: string;
  via: string[];  // where its local words came from (a deep talk, a project)
  ask: string | null;  // what it waits on the user for, as one sentence
}

// one conversation in the stream (core/conversation.py feed): what it is about, its turns, what it offered next
export interface Thread {
  thread: string; about: About | null; title: string | null; ts: string | null; lease_id: string | null;
  // spoken: Muninn brought it up unasked; handover: a deep talk's, as it came back (drawn as its card)
  turns: { role: "user" | "muninn"; text: string; ts: string | null; spoken?: boolean; files?: Sent[]; handover?: Handover; ended?: string; note?: boolean }[];  // ended: the deep talk it closes; note: a line saying where what is said goes
  suggest: Button[];
}

export interface Goal {
  id: string; title: string; period: string; state: string; state_label: string; value: string;
  actual: number | null; target: number; expected: number; unit: string; direction: "at_least" | "at_most"; pending: boolean;
}
export interface Deadline { id: string; title: string; due: string; days_left: number; manual: boolean; pending: string[]; in_lead_window: boolean }
export interface TrackerRow {
  target: string; title: string; status: string; applied: string | null; next_action: string | null; next_action_due: string | null; url: string | null;
}
export interface Domain {
  domain: string; title: string; priority: number;
  goals: Goal[]; deadlines: Deadline[]; tracker: TrackerRow[]; hour_goals: { id: string; title: string }[];
}
export type ProjectTodos = Partial<Record<"me" | "agent" | "pending", Record<string, string[]>>>;
export interface Today {
  now: string;
  domains: Domain[];
  domain_choices: { id: string; title: string }[];
  project_todos: ProjectTodos;
  statuses: string[];
  status_labels: Record<string, string>;
  unread: number;
  pending_confirmations: number;
}

export interface Day {
  now: string;
  events: { title: string; start: string; end: string; own: boolean }[];
  activity: { start: string; end: string; domain: string | null; title: string }[];
  hours: { title: string; hours: number }[];
  stays: { start: string; end: string; place: string | null }[];
}
export interface GoalDays { goals: Record<string, { day: string; value: number | null }[]> }

export interface Packet {
  summary?: string; match?: string[]; gaps?: string[]; resume?: string; resume_focus?: string;
  answers?: { q: string; a: string }[]; ask_first?: string[];
}
// an application form Muninn fills from a packet: unsupported | unread | gone | asking | filled | held | sent
export interface PreparedForm {
  state: string; site?: string; shot?: string; said?: string; blocked?: string; url?: string;
  fields?: { id: string; label: string; required: boolean; value?: string | string[]; ask?: string; file?: string }[];
}
export interface Prepared {
  id: number; status: string; kind: string; title: string; prepared_by: string; prepared_by_label: string; url: string | null;
  deadline: { title: string; days_left: number } | null;
  packet?: Packet; form?: PreparedForm; summary?: string; action?: string; draft?: string; from?: string; can_save_draft?: boolean;
  talk: Talk | null;  // the latest discussion about it
}
export interface Mail {
  from: string | null; ts: string; subject: string | null; summary: string | null; category: string | null; action: string | null;
  text: string | null; kind: string; web_url: string | null;
}
export interface InboxItem { id: number; ts: string; domain: string | null; kind: string; title: string; body: string; data: Record<string, unknown> }
export interface Reflection {
  title: string; body: string;
  data?: { aggregate?: { sessions?: number; themes?: [string, number][]; repeated_commitments?: string[]; observed_hours_by_domain?: Record<string, number> } };
}
export interface Me { device: { name: string; created: string | null }; last_push_received: string | null }
export interface Connection { configured: boolean; authorized: boolean; status: string }
// how to set up each tracking app the phone may send its location from, and when a point last came
export interface ActivitySetup {
  collectors: { name: string; label: string; command: string; note: string }[];  // each computer's collector: a line to run there
  token: string; last_received: string | null; last_source: string | null;
}
export interface LocationSetup {
  apps: { name: string; label: string; link: string | null; note: string; fields: { label: string; value: string; secret: boolean }[] }[];
  last_received: string | null; last_app: string | null;
}
export interface HealthSetup {
  last: { steps?: { steps: number; at: string } | null; sleep?: { hours: number; at: string } | null };
  url: string; authorization: string; focus_url: string; focus_open: { name: string } | null;
}

// small answers shared by many routes
export interface Ok { ok: boolean }
export interface MaybeUndone { ok: boolean; text?: string; action_id?: number }  // done, and undoable when anything changed
export interface DoneUndoable { ok: boolean; text: string; action_id: number }
export interface HandoffMade { ok: boolean; handoff_id: number | null }
export interface ErrorReply { error: string }  // any 4xx

// Every route the pages call and what it answers when it succeeds (the web API contract: contracts/web-api.v1.schema.json
// is generated from this file by bin/web_api_schema.py, and tests/test_web_api.py holds the server to it).
// A field may be added to an answer at any time; one is never removed or retyped without changing this map.
export interface SetupChoice { impl: string; needs_key: boolean; base_url: string | null }
export interface SetupState { choices: SetupChoice[]; public_url: string | null; has_model: boolean; language: string }
export interface SetupModels { models: string[] }
export interface SetupSaved { ok: boolean; restarting: boolean }
// whether this computer's Tailscale can give Muninn an HTTPS address (url: the one it would be), else what is missing (said)
export interface HttpsState { ready: boolean; url?: string; why?: string; said?: string }
export interface HttpsMade { url: string; restarting: boolean }
export interface SetupPair { code: string; url: string; qr: string }  // qr: an SVG of the URL, for the phone's camera
// the folders Muninn reads activity from (as config says now) and the usual ones found on this computer, not listed
export type SourceKey = "workspace_roots" | "transcript_roots";
export interface Sources { workspace_roots: string[]; transcript_roots: string[]; suggested: Record<SourceKey, string[]> }
// what making a Google client needs: the address Google sends back to and the privacy page it asks for (both null
// without an HTTPS address), whether a client is kept, and whether it can be set here at all
export interface GoogleSetup { callback: string | null; privacy: string | null; configured: boolean; settable: boolean }
export interface FormFillingState { state: "idle" | "installing" | "failed" | "restarting" | "restart_yourself"; ready?: boolean; said?: string; refused?: string | null }

export interface Api {
  "POST /api/pair": Ok;
  "GET /api/setup": SetupState;
  "POST /api/setup/models": SetupModels;
  "POST /api/setup/save": SetupSaved;
  "POST /api/setup/pair": SetupPair;
  "POST /api/setup/pair-here": { code: string };
  "POST /api/devices/pair": SetupPair;
  "GET /api/devices/https": HttpsState;
  "POST /api/devices/https": HttpsMade;
  "GET /api/me": Me;
  "GET /api/settings": Settings;
  "POST /api/settings/language": SetupSaved;
  "GET /api/settings/sources": Sources;
  "POST /api/settings/sources": SetupSaved;
  "GET /api/settings/google": GoogleSetup;
  "POST /api/settings/google": SetupSaved;
  "GET /api/settings/form-filling": FormFillingState;
  "POST /api/settings/form-filling": FormFillingState;
  "GET /api/today": Today;
  "GET /api/day": Day;
  "GET /api/goal-days": GoalDays;
  "GET /api/home": Home;
  "GET /api/inbox": List<InboxItem>;
  "POST /api/inbox/read": MaybeUndone;
  "GET /api/items/{iid}": InboxItem;
  "POST /api/ideas/{n}/settle": MaybeUndone;
  "GET /api/confirmations": List<Confirmation>;
  "POST /api/confirmations/{cid}": Ok;
  "GET /api/connections": Record<string, Connection>;
  "POST /api/connections/{provider}": { url: string };
  "GET /api/mail/{account}/{mid}": Mail;
  "POST /api/command": CommandReply;
  "POST /api/uploads": Sent;
  "POST /api/command/act": CommandReply;
  "GET /api/conversation": List<Thread>;
  "GET /api/topics": List<TopicState>;
  "GET /api/waiting": List<Waiting>;
  "POST /api/tasks/{key}/close": Undoable;
  "GET /api/requests": Requests;
  "POST /api/handoffs/{hid}/cancel": Ok;
  "GET /api/review": List<ReviewItem>;
  "POST /api/review": DoneUndoable;
  "POST /api/review/domain": DoneUndoable;
  "POST /api/review/why": { answer: string };
  "GET /api/prepared/{pid}": Prepared;
  "POST /api/prepared/{pid}/send": { ok: boolean; text: string };
  "POST /api/prepared/{pid}/draft": Ok;
  "POST /api/prepared/{pid}/done": DoneUndoable;
  "POST /api/prepared/{pid}/dismiss": DoneUndoable;
  "POST /api/actions/{aid}/undo": Ok;
  "GET /api/attribution": List<{ repo: string; domain: string | null }>;
  "POST /api/attribution": { ok: boolean; reattributed: number };
  "POST /api/input/note": Ok;
  "POST /api/input/hours": Ok;
  "POST /api/tracker/status": { ok: boolean; change: number | null };
  "POST /api/tracker/undo/{change}": Ok;
  "POST /api/deadlines/done": Ok;
  "POST /api/schedule": { ok: boolean; blocks: string[] };
  "POST /api/places": DoneUndoable;
  "GET /api/push/key": { key: string };
  "POST /api/push/subscribe": Ok;
  "POST /api/push/test": Ok;
  "POST /api/push/ack": Ok;
  "POST /api/opened": { said: string[] };
  "GET /api/setup/health": HealthSetup;
  "GET /api/setup/location": LocationSetup;
  "GET /api/setup/activity": ActivitySetup;
  "GET /api/project-questions": List<ProjectQuestion>;
  "POST /api/project-questions/{mid}/answer": HandoffMade;
  "POST /api/project-questions/{mid}/dismiss": HandoffMade;
  "GET /api/leases": LeaseInfo;
  "POST /api/leases/attach": LeaseReply;
  "POST /api/leases/keepalive": Ok;
  "POST /api/leases/terminal": { command: string; lease_id: string };
  "POST /api/leases/detach": { lease_id: string; state: string };
  "POST /api/leases/work": { lease_id: string; mode: LeaseMode; note?: string };
  "POST /api/leases/discuss": { lease_id: string; mode: LeaseMode; note?: string };
  "GET /api/permission-requests/pending": List<PermissionRequest>;
  "POST /api/permission-requests/{rid}/decision": { ok: boolean; note: string | null };
  "POST /api/client-error": Ok;
  // whether commands of a kind the user allowed before run without asking (null: every command is asked)
  "GET /api/permissions/auto-commands": { on: boolean | null };
  "POST /api/permissions/auto-commands": { on: boolean | null };
}
