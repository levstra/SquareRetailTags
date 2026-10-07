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
      <div className="p-6 text-sm text-red-600">
        Sign-in failed: {auth.error.message}{" "}
        <button className="underline" onClick={() => void auth.signinRedirect({ state: { returnTo: window.location.pathname } })}>
          Try again
        </button>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return (
      <div className="p-6 text-sm text-gray-500">{isSigningOut() ? "Signing out…" : "Signing in…"}</div>
    );
  }
  return <>{children}</>;
}
