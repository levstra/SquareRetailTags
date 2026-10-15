import { useEffect, type ReactNode } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";
import { isSigningOut } from "./userManager";

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();

  useEffect(() => {
    if (isSigningOut()) return;
    if (!hasAuthParams() && !auth.isAuthenticated && !auth.activeNavigator && !auth.isLoading && !auth.error) {
      void auth.signinRedirect({ state: { returnTo: window.location.pathname } });
    }
  }, [auth]);

  if (auth.error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm text-bad">Sign-in failed: {auth.error.message}</p>
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
