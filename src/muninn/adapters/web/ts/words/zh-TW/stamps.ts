export const stamps = {
  clock: (d: Date) => d.toLocaleTimeString("zh-TW", { hour: "numeric", minute: "2-digit", hour12: true }),
  today: "今天",
  yesterday: "昨天",
  // a day before yesterday: its date and weekday, with the year when it is not this year's
  day: (d: Date, weekday: string, withYear: boolean) => {
    const md = `${d.getMonth() + 1}/${d.getDate()} 週${weekday}`;
    return withYear ? `${d.getFullYear()}/${md}` : md;
  },
  earlier: (n: number) => `昨天以前的對話 · ${n} 則`,
};
