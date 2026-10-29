import { useEffect, type ReactNode } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";
import { isSigningOut } from "./userManager";

// A callback whose state we never stored (stale tab, back button, a different
// browser) can't be completed, but the Cognito session it came from is still
// good: start sign-in again once and Cognito redirects straight back.
const RESTARTED_KEY = "esl.signin-restarted";
const STATE_MISSING = "No matching state found in storage";

function hasRestarted(): boolean {
  try {
    return sessionStorage.getItem(RESTARTED_KEY) !== null;
  } catch {
    return true;
  }
}

function restartedOnce(): boolean {
  try {
    if (sessionStorage.getItem(RESTARTED_KEY)) return true;
    sessionStorage.setItem(RESTARTED_KEY, "1");
  } catch {
    return true; // Can't remember the attempt, so don't risk a redirect loop.
  }
  return false;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();

  useEffect(() => {
    if (auth.isAuthenticated) {
      try {
        sessionStorage.removeItem(RESTARTED_KEY);
      } catch {
        // Nothing to clear.
      }
    }
  }, [auth.isAuthenticated]);

  useEffect(() => {
    if (auth.error?.message === STATE_MISSING && !restartedOnce()) {
      void auth.signinRedirect();
    }
  }, [auth]);

  useEffect(() => {
    if (isSigningOut()) return;
    if (!hasAuthParams() && !auth.isAuthenticated && !auth.activeNavigator && !auth.isLoading && !auth.error) {
      void auth.signinRedirect({ state: { returnTo: window.location.pathname } });
    }
  }, [auth]);

  // The error stays set while the restart navigates away; don't flash it.
  const staleCallback = auth.error?.message === STATE_MISSING;
  if (auth.error && (auth.activeNavigator || (staleCallback && !hasRestarted()))) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted">Signing in…</div>;
  }
  if (auth.error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm text-ink">
          {staleCallback
            ? "That sign-in page had expired. Tap Try again to sign in."
            : "We couldn't sign you in. Tap Try again, and if it keeps happening, send us a screenshot of this page."}
        </p>
        <p className="text-xs text-muted">{auth.error.message}</p>
        <button
          type="button"
          className="min-h-10 rounded-lg border border-line bg-surface px-3.5 text-sm text-muted hover:text-ink"
          onClick={() => void auth.signinRedirect({ state: { returnTo: window.location.pathname } })}
        >
          Try again
        </button>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted">
        {isSigningOut() ? "Signing out…" : "Signing in…"}
      </div>
    );
  }
  return <>{children}</>;
}
