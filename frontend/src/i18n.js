import russian from "./locales/ru.js";

export const LANGUAGE_KEY = "itrtg:language";
const supported = new Set(["en", "ru"]);
const reverse = new Map(Object.entries(russian).map(([key, value]) => [value, key]));
const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const templates = [];
for (const [key, value] of Object.entries(russian)) {
  if (!/\{\d+\}/.test(key)) continue;
  for (const pattern of [key, value]) {
    const indexes = [...pattern.matchAll(/\{(\d+)\}/g)].map((match) => Number(match[1]));
    templates.push({ key, indexes, regex: new RegExp(`^${pattern.split(/\{\d+\}/).map(escapeRegex).join("(.*?)")}$`, "s") });
  }
}

export function localeFor(language) {
  return language === "ru" ? "ru-RU" : "en-US";
}

export function readLanguage(storage) {
  try {
    const value = storage.getItem(LANGUAGE_KEY);
    return supported.has(value) ? value : "en";
  } catch { return "en"; }
}

export function writeLanguage(storage, language) {
  if (!supported.has(language)) return false;
  try { storage.setItem(LANGUAGE_KEY, language); return true; }
  catch { return false; }
}

export function translate(message, language = "en", values) {
  if (typeof message !== "string") return String(message ?? "");
  let key = message;
  let parameters = values;
  if (!Object.hasOwn(russian, key)) {
    key = reverse.get(message) || message;
    if (!Object.hasOwn(russian, key)) {
      for (const template of templates) {
        const match = template.regex.exec(message);
        if (!match) continue;
        key = template.key;
        parameters = [];
        template.indexes.forEach((index, position) => { parameters[index] = match[position + 1]; });
        break;
      }
    }
  }
  const translated = language === "ru" ? (russian[key] ?? key) : key;
  if (!parameters) return translated;
  return translated.replace(/\{(\d+)\}/g, (placeholder, index) => parameters[index] === undefined ? placeholder : String(parameters[index]));
}
