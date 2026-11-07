// What the shared pieces (cards, pages, the conversation, the panel) need from the screen: redraw, put words in
// the input, and what to do when this device is not paired. The entry point (app.ts) connects it.

import { api, Unpaired } from "./api.js";
import { toast } from "./dom.js";
import type { Settings, Today } from "./types.js";
import { w } from "./words.js";

export interface Host {
  render(): Promise<void>;
  settled(thing: string): void;
  prompt(text: string): void;
  unpaired(): void;
}

let host: Host | null = null;

export function connect(h: Host): void {
  host = h;
}

function current(): Host {
  if (!host) throw new Error("no screen connected");
  return host;
}

// draw what is on screen again (after the user did something)
export const render = (): Promise<void> => current().render();
// a thing that waited is done (the user just acted on it): off the list at once, back to the list if it was open
export const settled = (thing: string): void => current().settled(thing);
// the user acted on the thing `node` is in (its page carries data-thing): it leaves what waits at once while `act`
// tells the server, then `said` is said; a card shown elsewhere (in the conversation) is drawn again afterwards
export async function answered(node: Element, act: () => Promise<unknown>, said: string): Promise<void> {
  const thing = node.closest<HTMLElement>("[data-thing]")?.dataset.thing;
  if (thing) settled(thing);
  try {
    await act();
    toast(said);
  } catch (err) { handleError(err); }
  if (!thing) void render();
}
// put words in the input and focus it, for the user to finish
export const prompt = (text: string): void => current().prompt(text);

// what went wrong on the screen, kept in the server's log so a tap that seemed to do nothing can be traced
export function reportError(e: unknown, where: string): void {
  try {
    const message = e instanceof Error ? e.message : String(e);
    navigator.sendBeacon("/api/client-error", new Blob([JSON.stringify({ message, where })], { type: "application/json" }));
  } catch { /* the report is only for tracing */ }
}

export function handleError(e: unknown): void {
  if (e instanceof Unpaired) return current().unpaired();
  toast(e instanceof Error ? e.message : String(e));
}

// shared between views: the server's settings, today's progress, and the forms' unsaved choices
export const state: {
  today: Today | null; settings: Settings | null;
  hoursValue: number; hoursGoal: string | null; noteDomain: string; showDate: boolean;
} = { today: null, settings: null, hoursValue: 1, hoursGoal: null, noteDomain: "", showDate: false };

// who reads the user's material, as the server is set up: everything to the cloud model, or sensitive things to the local model only
export const cloudFirst = (): boolean => (state.settings || {}).routing === "cloud";
export const cloudName = (): string => (state.settings || {}).cloud_label || w.shell.cloudModel;
export const reader = (): string => (cloudFirst() ? cloudName() : w.shell.localModel);

export async function loadSettings(): Promise<void> {
  if (!state.settings) state.settings = await api<Settings>("/api/settings");
}

export async function loadToday(): Promise<Today> {
  state.today = await api<Today>("/api/today");
  updateBadges(state.today.unread);
  return state.today;
}

// the app's badge on the home screen: what is unread
export function updateBadges(unread: number | null): void {
  if (unread !== null && "setAppBadge" in navigator) {
    (unread ? navigator.setAppBadge(unread) : navigator.clearAppBadge()).catch(() => {});
  }
}
