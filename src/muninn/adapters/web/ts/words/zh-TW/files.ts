export const files = {
  attach: "附上照片或檔案",
  notSent: (name: string, why: string) => `「${name}」沒傳上去${why ? ":" + why : ""}`,
  tooMany: (max: number) => `一次最多 ${max} 個檔案`,
  notTaken: (name: string) => `「${name}」這種檔案收不了:照片、PDF、純文字檔可以`,
  remove: (name: string) => `拿掉 ${name}`,
  failed: "沒傳上去",
};
