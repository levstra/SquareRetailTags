import { useCallback, useSyncExternalStore } from "react";

/** Storage key — also read by the boot script in index.html, which resolves
 * the theme before first paint. Change it in both places or not at all. */
const STORAGE_KEY = "esl.theme";

/** What the user picked. "system" follows the OS and is the default. */
export type ThemeChoice = "light" | "dark" | "system";

/** What is actually on screen. */
export type Theme = "light" | "dark";

function readChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Private mode, or site data blocked. Fall through to "system".
  }
  return "system";
}

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/* The theme is one global thing, so it lives in one module-level store rather
 * than in a component's state: every useTheme() caller reads the same value
 * and re-renders together. */

let choice: ThemeChoice = readChoice();
let theme: Theme = choice === "system" ? systemTheme() : choice;

const listeners = new Set<() => void>();

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

/** The whole mechanism: one attribute on <html>. Every color in the app is a
 * CSS variable keyed off it (see index.css), so this is the only write needed
 * to re-theme the page — no component restyles itself. */
function apply() {
  theme = choice === "system" ? systemTheme() : choice;
  document.documentElement.dataset.theme = theme;
  listeners.forEach((l) => l());
}

// Follow the OS while the choice is "system". Registered once, for the life of
// the page: the listener checks the current choice itself.
window
  .matchMedia("(prefers-color-scheme: dark)")
  .addEventListener("change", () => {
    if (choice === "system") apply();
  });

function setChoice(next: ThemeChoice) {
  choice = next;
  try {
    if (next === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Can't persist; the choice still applies for this session.
  }
  apply();
}

export function useTheme() {
  const current = useSyncExternalStore(
    subscribe,
    () => choice,
    () => "system" as ThemeChoice,
  );
  const resolved = useSyncExternalStore(
    subscribe,
    () => theme,
    () => "light" as Theme,
  );
  return { choice: current, theme: resolved, choose: useCallback(setChoice, []) };
}
