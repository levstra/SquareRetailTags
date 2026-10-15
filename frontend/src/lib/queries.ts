import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { components } from "../api/schema.d.ts";

export type Tag = components["schemas"]["Tag"];
export type Variation = components["schemas"]["Variation"];
export type Store = components["schemas"]["Store"];

/** Tag IDs are 12-char uppercase hex at the API boundary — the real ESL
 * hardware's id length. Validate before sending, so a typo fails here with a
 * readable message instead of as a 422. */
export const TAG_ID_RE = /^[0-9A-F]{12}$/;

/** The server's `detail` when there is one, else the error's own message. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}

/** Tags this user may see: every store for owners, allowed stores for staff. */
export function useTags() {
  return useQuery({
    queryKey: ["tags"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/tags");
      if (!response.ok || !data) throw new ApiError(response, error);
      return data.tags;
    },
    refetchInterval: 5_000,
  });
}

/** Stores this user may use. They come from the business's Square locations. */
export function useStores() {
  return useQuery({
    queryKey: ["stores"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/stores");
      if (!response.ok || !data) throw new ApiError(response, error);
      return data.stores;
    },
  });
}

export function useVariations() {
  return useQuery({
    queryKey: ["variations"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/catalog/variations");
      if (!response.ok || !data) throw new ApiError(response, error);
      return data.variations;
    },
  });
}

/** Store id → name, for rows that only carry a store id. */
export function useStoreNames(): (storeId: string | null | undefined) => string {
  const { data } = useStores();
  return (storeId) => data?.find((s) => s.id === storeId)?.name ?? "Unknown store";
}
