export const format = {
  // Sunday first, as Date.getDay() counts
  weekdays: ["日", "一", "二", "三", "四", "五", "六"] as readonly string[],
  day: (month: number, date: number, weekday: string) => `${month} 月 ${date} 日 · 週${weekday}`,
  timeLocale: "zh-TW",
  project: "專案",
  mailLabel: { application_confirmation: "投遞確認", interview: "面試", rejection: "拒絕", offer: "offer",
    needs_reply: "要回覆", deadline: "期限", fyi: "參考", promo: "行銷" } as Record<string, string>,
};
