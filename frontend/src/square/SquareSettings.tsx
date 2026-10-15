import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Button, StatusLabel } from "../ui";
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
    <section className="max-w-xl">
      <h1 className="text-3xl font-bold tracking-tight text-ink">Square</h1>
      <p className="mt-1.5 mb-7 text-[15px] text-muted">
        Where prices come from, and which locations have shelf labels.
      </p>

      <div className="flex flex-col gap-5">
      {banner && (
        <p className={`rounded-lg px-3 py-2 text-sm ${banner.ok ? "bg-ok-tint text-ok" : "bg-bad-tint text-bad"}`}>
          {banner.text}
        </p>
      )}

      <div className="flex flex-col gap-2.5 rounded-xl border border-line bg-surface p-5">
        <h2 className="text-xs font-bold tracking-wider text-muted uppercase">Square account</h2>
        {connection.isPending ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : (
          status && (
            <StatusLabel tone={active ? "ok" : status === "not_connected" ? "idle" : "bad"}>
              {STATUS_TEXT[status]}
              {connection.data?.merchant_id ? (
                <span className="font-mono text-xs font-normal text-faint">
                  {connection.data.merchant_id}
                </span>
              ) : null}
            </StatusLabel>
          )
        )}
        {connection.isError && <p className="text-sm text-bad">{describeError(connection.error)}</p>}
        {status != null && !active && (
          <Button
            variant="primary"
            className="self-start"
            disabled={connect.isPending}
            onClick={() => connect.mutate()}
          >
            {status === "not_connected" ? "Connect Square" : "Reconnect Square"}
          </Button>
        )}
        <p className="text-xs text-faint">Read-only access: catalog items and locations. We never change anything in Square.</p>
      </div>

      {active && (
        <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-5">
          <h2 className="text-xs font-bold tracking-wider text-muted uppercase">Stores</h2>
          <p className="text-sm text-muted">Choose the Square locations that have shelf labels.</p>
          {locations.isLoading && <p className="text-sm text-faint">Loading locations…</p>}
          {locationsError && <p className="text-sm text-bad">{locationsError}</p>}
          {(locations.data ?? []).map((l) => (
            <label key={l.id} className="flex min-h-10 items-center gap-2.5 text-sm text-ink">
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
              {l.store_id != null && <span className="text-xs text-faint">in use</span>}
            </label>
          ))}
          <Button
            variant="primary"
            className="self-start"
            disabled={save.isPending || chosen.length === 0 || selected === null}
            onClick={() => save.mutate(chosen)}
          >
            {save.isPending ? "Saving…" : "Save stores"}
          </Button>
        </div>
      )}

      {error && <p className="rounded-lg bg-bad-tint px-3 py-2 text-sm text-bad">{error}</p>}
      </div>
    </section>
  );
}
