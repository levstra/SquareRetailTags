import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "react-oidc-context";
import "./index.css";
import App from "./App.tsx";
import { RequireAuth } from "./auth/RequireAuth";
import { userManager } from "./auth/userManager";

const queryClient = new QueryClient();

// Drop ?code=&state= from the URL once the callback is processed.
const onSigninCallback = () => {
  window.history.replaceState({}, document.title, "/");
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider userManager={userManager} onSigninCallback={onSigninCallback}>
      <QueryClientProvider client={queryClient}>
        <RequireAuth>
          <App />
        </RequireAuth>
      </QueryClientProvider>
    </AuthProvider>
  </StrictMode>,
);
