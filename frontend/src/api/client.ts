import createClient, { type Middleware } from "openapi-fetch";
import { userManager } from "../auth/userManager";
import type { paths } from "./schema.d.ts";

const bearer: Middleware = {
  async onRequest({ request }) {
    const user = await userManager.getUser();
    if (user && !user.expired) {
      request.headers.set("Authorization", `Bearer ${user.access_token}`);
    }
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) {
      await userManager.signinRedirect();
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "/v1" });
api.use(bearer);
