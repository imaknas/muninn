import type { Words } from "../../words.js";

export const things: Words["things"] = {
  // what kind of thing each one is, as its one-line state says it
  kinds: {
    relink: "Access expired", lease: "In a deep talk", perm: "A project's request", talkq: "Deep talk questions", project: "A project asks you",
    action: "Asking you first", plan_change: "Change the plan?", status_from_mail: "Update from mail", fact_done: "What you just said",
    attribution: "Repo assignment", window_class: "Window assignment", offline_time: "Free time", question: "A quick question",
    review: "Plan review", packet: "Application packet", reply: "Reply draft", due: "Task due", topic: "Being discussed",
  },
  // ── When a task is due ──
  monthDay: (d: Date) => d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  yesterday: "yesterday",
  daysAgo: (n: number) => `${n} days ago`,
  today: "today",
  tomorrow: "tomorrow",
  weekday: (day: string) => day,
  // ── Titles and states ──
  needsRelink: (name: string) => `${name} needs reconnecting`,
  asksYou: (who: string) => `${who} asks you`,
  wantsToChange: (what?: string) => `Wants to change ${what || "a file"}`,
  wantsToRun: (what?: string) => `Wants to run ${what || "a command"}`,
  wantsToOpen: (what?: string) => `Wants to open ${what || "a web page"}`,
  wantsToDo: (what?: string) => `Wants to: ${what || "do something"}`,
  firstOfMore: (first: string, more: number) => `${first} (${more} more)`,
  leftByTalk: (by: string | null) => (by ? `Left by ${by}'s deep talk` : "Left by a deep talk"),
  whereActivityGoes: (match: string) => `Where does the activity in ${match} belong?`,
  reviewTitle: (title: string, detail: string) => `${title}: ${detail}`,
  replyDraftFrom: (from: string) => `Reply draft · ${from}`,
  packetReady: (by: string) => `Application packet · prepared by ${by}`,
  waitingOnYou: "Waiting on you",
  inProgress: "In progress",
  deepTalkWith: (who: string) => `Deep talk with ${who}`,
  quietAWhile: "Quiet for a while",
  inTerminal: "Continuing in the terminal",
  going: "In progress",
  backToThisConversation: "Back to this conversation",
};
