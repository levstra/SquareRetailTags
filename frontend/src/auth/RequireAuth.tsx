import { useEffect, type ReactNode } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();

  useEffect(() => {
    if (!hasAuthParams() && !auth.isAuthenticated && !auth.activeNavigator && !auth.isLoading && !auth.error) {
      void auth.signinRedirect();
    }
  }, [auth]);

  if (auth.error) {
    return (
      <div className="p-6 text-sm text-red-600">
        Sign-in failed: {auth.error.message}{" "}
        <button className="underline" onClick={() => void auth.signinRedirect()}>
          Try again
        </button>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return <div className="p-6 text-sm text-gray-500">Signing in…</div>;
  }
  return <>{children}</>;
}
