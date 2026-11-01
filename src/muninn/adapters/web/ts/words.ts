// What the page says, in the language Muninn speaks (the page's <html lang>, set by the server from config).
// Each language is a catalog of the same shape (words/<lang>.ts, one module per screen): a text is a string or,
// when the wording depends on a value, a function of it. A catalog missing a text, or with one of another shape,
// does not compile.

import { zh } from "./words/zh-TW.js";
import { en } from "./words/en.js";

export type Words = typeof zh;
export const LANGUAGES = { "zh-TW": zh, en } as const satisfies Record<string, Words>;
export type Lang = keyof typeof LANGUAGES;
// each language's own name for itself, for choosing one
export const LANGUAGE_NAMES: Record<Lang, string> = { "zh-TW": "繁體中文", en: "English" };

const known = (code: string): code is Lang => code in LANGUAGES;

export let lang: Lang = known(document.documentElement.lang) ? document.documentElement.lang as Lang : "zh-TW";
export let w: Words = LANGUAGES[lang];

// the setup screen, before a language is saved, shows the one being chosen
export function useLanguage(code: Lang): void {
  lang = code;
  w = LANGUAGES[code];
  document.documentElement.lang = code;
}
