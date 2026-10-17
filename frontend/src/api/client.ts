import createClient, { type Middleware } from "openapi-fetch";
import { isSigningOut, userManager } from "../auth/userManager";
import type { paths } from "./schema.d.ts";

let redirecting = false;

// A 401 that survives a fresh sign-in means the server is rejecting our tokens
// (misconfiguration), not that we're signed out. Don't bounce forever.
const REDIRECT_KEY = "esl.lastSigninRedirect";
const REDIRECT_COOLDOWN_MS = 60_000;

function recentlyRedirected(): boolean {
  try {
    const at = Number(sessionStorage.getItem(REDIRECT_KEY));
    return at > 0 && Date.now() - at < REDIRECT_COOLDOWN_MS;
  } catch {
    return false;
  }
}

function markRedirected(): void {
  try {
    sessionStorage.setItem(REDIRECT_KEY, String(Date.now()));
  } catch {
    // storage unavailable: fall back to the in-memory flag only
  }
}

export function clearRedirectMarker(): void {
  try {
    sessionStorage.removeItem(REDIRECT_KEY);
  } catch {
    // ignore
  }
}

const bearer: Middleware = {
  async onRequest({ request }) {
    const user = await userManager.getUser();
    if (user && !user.expired) {
      request.headers.set("Authorization", `Bearer ${user.access_token}`);
    }
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401 && !redirecting && !isSigningOut() && !recentlyRedirected()) {
      redirecting = true;
      markRedirected();
      await userManager.signinRedirect({ state: { returnTo: window.location.pathname } });
    }
    return response;
  },
};

// A failed API call, keeping the HTTP status so callers can branch on it
// instead of on the server's wording.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(response: Response, body: unknown) {
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : null;
    super(detail ?? `Request failed (${response.status})`);
    this.status = response.status;
    this.detail = detail;
  }
}

export const api = createClient<paths>({ baseUrl: "/v1" });
api.use(bearer);
