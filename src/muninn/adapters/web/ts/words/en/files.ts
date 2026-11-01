import type { Words } from "../../words.js";

export const files: Words["files"] = {
  attach: "Attach a photo or file",
  notSent: (name: string, why: string) => `"${name}" didn't upload${why ? ": " + why : ""}`,
  tooMany: (max: number) => `Up to ${max} files at a time`,
  notTaken: (name: string) => `Can't take a file like "${name}": photos, PDFs and plain text files work`,
  remove: (name: string) => `Remove ${name}`,
  failed: "Didn't upload",
};
