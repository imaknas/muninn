import type { Words } from "../../words.js";

export const app: Words["app"] = {
  conversation: "Conversation with Muninn",
  sayLabel: "Talk to Muninn",
  sayPlaceholder: "Talk to Muninn…",
  send: "Send",
  panel: "Today and details",
  pillLabel: (waiting: number, today: number) => `${waiting ? `${waiting} waiting on you, ` : ""}${today} today; open to see all and details`,
  pillWaiting: (waiting: number) => `${waiting} waiting on you`,
  pillToday: (today: number) => `${today} today`,
  pairTitle: "Pair this device",
  pairHow: "On the computer running Muninn, run muninn pair and enter the 8 characters it shows here. Each device pairs only once.",
  pairHomeScreen: "Best to first use Safari's \"Share → Add to Home Screen\", then open Muninn from the home screen to pair; the home screen app and Safari are signed in separately.",
  pairCode: "Pairing code",
  deviceNamePlaceholder: "Device name (e.g. iPhone)",
  deviceName: "Device name",
  pair: "Pair",
};
