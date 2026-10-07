import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { User } from "oidc-client-ts";
import { AuthProvider } from "react-oidc-context";
import "./index.css";
import { RequireAuth } from "./auth/RequireAuth";
import { userManager } from "./auth/userManager";
import { Root } from "./Root";

const queryClient = new QueryClient();

// Drop ?code=&state= and go back to the page that asked for sign-in.
const onSigninCallback = (user: User | undefined) => {
  const returnTo = (user?.state as { returnTo?: string } | undefined)?.returnTo;
  const safe = returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//") && returnTo !== "/auth/callback";
  window.history.replaceState({}, document.title, safe ? returnTo : "/");
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider userManager={userManager} onSigninCallback={onSigninCallback}>
      <QueryClientProvider client={queryClient}>
        <RequireAuth>
          <Root />
        </RequireAuth>
      </QueryClientProvider>
    </AuthProvider>
  </StrictMode>,
);
