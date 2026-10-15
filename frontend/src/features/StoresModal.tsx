import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { relativeTime } from "../lib/format";
import { errorMessage, useStores, type Store } from "../lib/queries";
import { Button, Modal, StatusLabel } from "../ui";

/** A gateway that polled within this window counts as online. The Pi
 * long-polls every few seconds and posts telemetry every minute. */
const ONLINE_WITHIN_MS = 5 * 60_000;

function GatewayStatus({ store }: { store: Store }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["store-gateways", store.id],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/stores/{store_id}/gateways", {
        params: { path: { store_id: store.id } },
      });
      if (!response.ok || !data) throw new ApiError(response, error);
      return data;
    },
    refetchInterval: 30_000,
  });

  if (isLoading) return <p className="text-xs text-faint">Checking gateway…</p>;
  if (error) return <p className="text-xs text-bad">{errorMessage(error)}</p>;

  const gateways = (data?.gateways ?? []).filter((g) => !g.revoked);
  if (gateways.length === 0) {
    return (
      <StatusLabel tone="idle">No gateway installed</StatusLabel>
    );
  }

  const aps = data?.access_points ?? [];
  const apsUp = aps.filter((ap) => ap.connected && !ap.stale).length;

  return (
    <div className="flex flex-col gap-1">
      {gateways.map((g) => {
        const online =
          g.last_seen_at != null &&
          Date.now() - new Date(g.last_seen_at).getTime() < ONLINE_WITHIN_MS;
        return (
          <div key={g.id}>
            <StatusLabel tone={online ? "ok" : "bad"}>
              {g.name}: {online ? "online" : "offline"}
            </StatusLabel>
            <p className="text-xs text-faint">
              {g.last_seen_at ? `last polled ${relativeTime(g.last_seen_at)}` : "never polled"}
              {" · "}
              <span className="font-mono">{g.key_prefix}</span>
            </p>
          </div>
        );
      })}
      {aps.length > 0 && (
        <p className="text-xs text-muted">
          {apsUp} of {aps.length} access point{aps.length === 1 ? "" : "s"} connected
        </p>
      )}
    </div>
  );
}

/** Read-only. Stores come from the business's Square locations (owners choose
 * them on the Square screen); gateway keys are issued by the installer with
 * `esl admin add-gateway` and never shown in the browser. */
export function StoresModal({
  isOwner,
  onClose,
}: {
  isOwner: boolean;
  onClose: () => void;
}) {
  const { data: storeData, error } = useStores();
  const { data: locationData } = useQuery({
    queryKey: ["square-locations"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/square/locations");
      if (!response.ok || !data) throw new ApiError(response, error);
      return data.locations;
    },
    enabled: isOwner,
  });

  const stores = storeData ?? [];
  const locationName = (storeId: string) =>
    locationData?.find((l) => l.store_id === storeId)?.name;

  return (
    <Modal
      title="Stores"
      onClose={onClose}
      wide
      footer={
        <Button variant="quiet" onClick={onClose}>
          Close
        </Button>
      }
    >
      {error && <p className="mb-4 text-sm text-bad">{errorMessage(error)}</p>}

      <ul className="mb-5 flex list-none flex-col gap-0 p-0">
        {stores.length === 0 && (
          <li className="py-2 text-sm text-faint">No stores yet.</li>
        )}
        {stores.map((s) => {
          const location = locationName(s.id);
          return (
            <li
              key={s.id}
              className="flex flex-wrap items-start justify-between gap-3 border-b border-line-soft py-3 last:border-0"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-ink">{s.name}</p>
                {location && (
                  <p className="mt-0.5 text-xs text-faint">Square location: {location}</p>
                )}
              </div>
              {isOwner && <GatewayStatus store={s} />}
            </li>
          );
        })}
      </ul>

      <p className="text-xs text-muted">
        {isOwner
          ? "Stores come from your Square locations. Choose them on the Square screen. Gateways are set up by your installer."
          : "Stores come from the business's Square locations. Ask the owner if one is missing."}
      </p>
    </Modal>
  );
}
