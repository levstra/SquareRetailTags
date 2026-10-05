import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { squareConnectionQuery } from "./queries";

const RESULT_MESSAGES: Record<string, { text: string; ok: boolean }> = {
  connected: { text: "Square is connected. Your catalog is syncing now.", ok: true },
  error: { text: "Square didn't connect. Try again.", ok: false },
  merchant_in_use: { text: "That Square account is already linked to another business.", ok: false },
  different_merchant: {
    text: "This business is linked to a different Square account. Sign in to that Square account and try again.",
    ok: false,
  },
};

const STATUS_TEXT: Record<string, string> = {
  not_connected: "Not connected",
  active: "Connected",
  needs_reconnect: "Needs reconnecting",
  revoked: "Access was revoked in Square",
};

// Branch on the HTTP status, never on the server's wording.
function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return "Square isn't connected, or it rejected our access. Reconnect Square to continue.";
    if (e.status === 502) return "Square is unavailable right now. Try again in a minute.";
    if (e.status === 400) return "That selection isn't valid. Choose at least one location and try again.";
    if (e.status === 403) return "Only the business owner can change Square settings.";
  }
  return "Something went wrong. Try again.";
}

export function SquareSettings({ result }: { result: string | null }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  const connection = useQuery(squareConnectionQuery);
  const active = connection.data?.status === "active";

  // A 409 means Square isn't connected or rejected the token (the connection
  // is now needs_reconnect): refresh it so the page flips to the reconnect state.
  const handle409 = (e: unknown) => {
    if (e instanceof ApiError && e.status === 409) {
      void queryClient.invalidateQueries({ queryKey: squareConnectionQuery.queryKey });
    }
  };

  const locations = useQuery({
    queryKey: ["square-locations"],
    enabled: active,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/square/locations");
      if (!response.ok || !data) {
        const failure = new ApiError(response, error);
        handle409(failure);
        throw failure;
      }
      return data.locations;
    },
  });

  const linked = (locations.data ?? []).filter((l) => l.store_id).map((l) => l.id);
  const chosen = selected ?? linked;

  const connect = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/square/oauth/start");
      if (!response.ok || !data) throw new ApiError(response, error);
      // Same window: the state cookie is SameSite=Lax and Square returns here.
      window.location.assign(data.authorize_url);
    },
    onError: (e) => setError(describeError(e)),
  });

  const save = useMutation({
    mutationFn: async (ids: string[]) => {
      const { error, response } = await api.PUT("/stores/selection", { body: { location_ids: ids } });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: () => {
      setError("");
      setSelected(null);
      void queryClient.invalidateQueries({ queryKey: ["square-locations"] });
      void queryClient.invalidateQueries({ queryKey: ["stores"] });
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      // Linking a store changes which location price its tags show.
      void queryClient.invalidateQueries({ queryKey: ["tags"] });
    },
    onError: (e) => {
      handle409(e);
      setError(describeError(e));
    },
  });

  const banner = result ? RESULT_MESSAGES[result] : undefined;
  const status = connection.data?.status;
  const locationsError = locations.error ? describeError(locations.error) : "";

  return (
    <section className="max-w-xl space-y-6">
      {banner && (
        <p className={`rounded px-3 py-2 text-sm ${banner.ok ? "bg-green-50 text-green-800" : "bg-red-50 text-red-700"}`}>
          {banner.text}
        </p>
      )}

      <div className="rounded border border-gray-200 p-4 space-y-2">
        <h2 className="font-medium">Square account</h2>
        <p className="text-sm text-gray-600">
          {connection.isPending
            ? "Loading…"
            : status
              ? STATUS_TEXT[status]
              : ""}
          {connection.data?.merchant_id ? ` · ${connection.data.merchant_id}` : ""}
        </p>
        {connection.isError && <p className="text-sm text-red-600">{describeError(connection.error)}</p>}
        {status != null && !active && (
          <button
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={connect.isPending}
            onClick={() => connect.mutate()}
          >
            {status === "not_connected" ? "Connect Square" : "Reconnect Square"}
          </button>
        )}
        <p className="text-xs text-gray-400">Read-only access: catalog items and locations. We never change anything in Square.</p>
      </div>

      {active && (
        <div className="rounded border border-gray-200 p-4 space-y-3">
          <h2 className="font-medium">Stores</h2>
          <p className="text-sm text-gray-600">Choose the Square locations that have shelf tags.</p>
          {locations.isLoading && <p className="text-sm text-gray-400">Loading locations…</p>}
          {locationsError && <p className="text-sm text-red-600">{locationsError}</p>}
          {(locations.data ?? []).map((l) => (
            <label key={l.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={chosen.includes(l.id)}
                // Stores can't be removed here: removing one would delete its tags.
                disabled={l.store_id != null}
                onChange={(e) =>
                  setSelected(e.target.checked ? [...chosen, l.id] : chosen.filter((id) => id !== l.id))
                }
              />
              {l.name}
              {l.store_id != null && <span className="text-xs text-gray-400">in use</span>}
            </label>
          ))}
          <button
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={save.isPending || chosen.length === 0 || selected === null}
            onClick={() => save.mutate(chosen)}
          >
            {save.isPending ? "Saving…" : "Save stores"}
          </button>
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
    </section>
  );
}
