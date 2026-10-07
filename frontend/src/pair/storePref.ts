const KEY = "esl.pair.lastStore";

// Per-device convenience only; storage can be missing or throw (private mode).
export function getLastStore(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setLastStore(id: string): void {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // Not remembered; the picker still works.
  }
}
