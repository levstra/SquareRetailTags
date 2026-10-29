import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const authority = import.meta.env.VITE_COGNITO_AUTHORITY;
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN;
const origin = window.location.origin;

// Cognito hosted UI, authorization code + PKCE. Tokens never touch our backend
// except as the bearer header on API calls.
export const userManager = new UserManager({
  authority,
  // Explicit rather than discovered: the discovery document may advertise the
  // amazoncognito.com prefix domain, and sign-in should stay on our own host.
  metadata: {
    issuer: authority,
    jwks_uri: `${authority}/.well-known/jwks.json`,
    authorization_endpoint: `${cognitoDomain}/oauth2/authorize`,
    token_endpoint: `${cognitoDomain}/oauth2/token`,
    userinfo_endpoint: `${cognitoDomain}/oauth2/userInfo`,
    revocation_endpoint: `${cognitoDomain}/oauth2/revoke`,
  },
  client_id: clientId,
  redirect_uri: `${origin}/auth/callback`,
  post_logout_redirect_uri: `${origin}/`,
  response_type: "code",
  scope: "openid email",
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.localStorage }),
  // The default (sessionStorage) is per tab. Phones often land the Cognito
  // redirect in a different tab than the one that started sign-in, and the
  // callback then fails with "No matching state found in storage".
  stateStore: new WebStorageStateStore({ store: window.localStorage }),
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
