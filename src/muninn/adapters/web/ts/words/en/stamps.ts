import type { Words } from "../../words.js";

export const stamps: Words["stamps"] = {
  clock: (d: Date) => d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true }),
  today: "Today",
  yesterday: "Yesterday",
  // a day before yesterday: its date and weekday, with the year when it is not this year's
  day: (d: Date, weekday: string, withYear: boolean) =>
    `${weekday}, ${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })}${withYear ? `, ${d.getFullYear()}` : ""}`,
  earlier: (n: number) => `Earlier conversation · ${n} ${n === 1 ? "message" : "messages"}`,
};
