import { useSyncExternalStore } from "react";
import { japanese } from "./locales/ja";

export type Locale = "en" | "zh" | "ja";
const languageTags: Record<Locale, string> = { en: "en", zh: "zh-CN", ja: "ja" };
const listeners = new Set<() => void>();
let current: Locale = "en";

export function resolveLocale(saved: string | null, browserLanguage = "en"): Locale {
  if (saved === "en" || saved === "zh" || saved === "ja") return saved;
  const language = browserLanguage.toLowerCase();
  return language.startsWith("zh") ? "zh" : language.startsWith("ja") ? "ja" : "en";
}

function read() {
  if (typeof window === "undefined") return "en" as const;
  try { current = resolveLocale(localStorage.getItem("lo-locale"), navigator.language); } catch { /* retain in-memory choice */ }
  return current;
}

function notify() {
  read();
  document.documentElement.lang = languageTags[current];
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  if (!listeners.size) {
    window.addEventListener("lo-locale-change", notify);
    window.addEventListener("storage", notify);
  }
  listeners.add(listener);
  document.documentElement.lang = languageTags[read()];
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      window.removeEventListener("lo-locale-change", notify);
      window.removeEventListener("storage", notify);
    }
  };
}

export function changeLocale(next: Locale) {
  current = next;
  try { localStorage.setItem("lo-locale", next); } catch { /* retain in-memory choice */ }
  window.dispatchEvent(new Event("lo-locale-change"));
}

/** Translate UI copy only, never prompts, model output, logs or user content.
 * Numbered slots keep runtime values intact and out of the catalog. */
export function translate(locale: Locale, english: string, ...values: unknown[]): string {
  const template = locale === "ja" ? japanese[english] ?? english : english;
  return template.replace(/\{(\d+)\}/g, (slot, index: string) =>
    Number(index) < values.length ? String(values[Number(index)]) : slot);
}

export function useLocale() {
  const locale = useSyncExternalStore(subscribe, read, () => "en" as Locale);
  // The translator uses this component's snapshot, including the English
  // server snapshot during hydration. Never read localStorage inside render
  // helpers: other components may still be hydrating after a locale update.
  const ja = (english: string, ...values: unknown[]) => translate(locale, english, ...values);
  return { locale, zh: locale === "zh", ja, change: changeLocale };
}
