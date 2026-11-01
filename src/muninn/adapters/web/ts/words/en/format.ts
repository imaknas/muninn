import type { Words } from "../../words.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const format: Words["format"] = {
  weekdays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
  day: (month: number, date: number, weekday: string) => `${weekday}, ${MONTHS[month - 1]} ${date}`,
  timeLocale: "en-GB",
  project: "the project",
  mailLabel: { application_confirmation: "Application received", interview: "Interview", rejection: "Rejection", offer: "Offer",
    needs_reply: "Needs reply", deadline: "Deadline", fyi: "FYI", promo: "Marketing" },
};
