import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN;
const origin = window.location.origin;

// Cognito hosted UI, authorization code + PKCE. Tokens never touch our backend
// except as the bearer header on API calls.
export const userManager = new UserManager({
  authority: import.meta.env.VITE_COGNITO_AUTHORITY,
  client_id: clientId,
  redirect_uri: `${origin}/auth/callback`,
  post_logout_redirect_uri: `${origin}/`,
  response_type: "code",
  scope: "openid email",
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.localStorage }),
});

// Cognito's logout endpoint isn't in its OIDC discovery document.
let signingOut = false;
export function isSigningOut(): boolean {
  return signingOut;
}

export async function signOut(): Promise<void> {
  signingOut = true;
  await userManager.removeUser();
  const params = new URLSearchParams({ client_id: clientId, logout_uri: `${origin}/` });
  window.location.assign(`${cognitoDomain}/logout?${params}`);
}
