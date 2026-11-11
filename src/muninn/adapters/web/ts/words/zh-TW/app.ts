export const app = {
  // the page's fixed parts (index.html carries the same text, for before the script runs)
  conversation: "跟 Muninn 的對話",
  sayLabel: "跟 Muninn 說",
  sayPlaceholder: "跟 Muninn 說⋯",
  send: "送出",
  panel: "今天的事與細節",
  // the button at the top
  pillLabel: (waiting: number, today: number) => `${waiting ? `${waiting} 件等你,` : ""}今天 ${today} 件;點開看全部和細節`,
  pillWaiting: (waiting: number) => `${waiting} 件等你`,
  pillToday: (today: number) => `今天 ${today} 件`,
  pillReading: "讀取中",
  pillUnread: "沒讀到",
  pillUnreadLabel: "沒讀到等你的事;點一下再讀一次",
  // pairing
  pairTitle: "配對這台裝置",
  pairHow: "在跑 Muninn 的電腦上執行 muninn pair,把顯示的 8 碼輸入這裡。每台裝置只要配對一次。",
  pairHomeScreen: "建議先用 Safari 的「分享 → 加入主畫面」,再從主畫面開 Muninn 配對;主畫面 App 跟 Safari 的登入是分開的。",
  pairCode: "配對碼",
  deviceNamePlaceholder: "裝置名稱(例如 iPhone)",
  deviceName: "裝置名稱",
  pair: "配對",
};
