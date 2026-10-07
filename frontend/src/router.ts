import { useSyncExternalStore } from "react";

// Two screens don't need a router library. /auth/callback is consumed by
// react-oidc-context before any route renders.
const listeners = new Set<() => void>();

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("popstate", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("popstate", onChange);
  };
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

function notifyAll(): void {
  listeners.forEach((notify) => notify());
}

/** Replace the current history entry (no back-button step) and re-render usePath readers. */
export function replacePath(path: string): void {
  window.history.replaceState({}, document.title, path);
  notifyAll();
}

export function navigate(path: string): void {
  if (path === window.location.pathname) return;
  window.history.pushState({}, "", path);
  notifyAll();
}
