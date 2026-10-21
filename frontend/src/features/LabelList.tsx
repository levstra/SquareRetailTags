import { useMutation } from "@tanstack/react-query";
import { ChevronDown, Lightbulb } from "lucide-react";
import { useMemo, useState } from "react";
import clsx from "clsx";
import { api, ApiError } from "../api/client";
import { formatPrice, relativeTime, tagStatus, tagVariationLabel } from "../lib/format";
import { useStoreNames, useStores, useTags, type Tag } from "../lib/queries";
import { toneEdge } from "../lib/tone";
import {
  Button,
  Count,
  Empty,
  ErrorState,
  Loading,
  SearchInput,
  Select,
  StatusLabel,
} from "../ui";

/** Flashes a label's LED so staff can find it on the shelf.
 *
 * Queued, not immediate: the store gateway is LAN-only and can't be called
 * from here, so it picks the command up on its next poll. The button says
 * "Queued" rather than "Flashing" for that reason. */
function LocateButton({ tagId }: { tagId: string }) {
  const [state, setState] = useState<"idle" | "queued" | "error">("idle");

  const mutation = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST("/tags/{tag_id}/locate", {
        params: { path: { tag_id: tagId } },
        body: { seconds: 30 },
      });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: () => {
      setState("queued");
      setTimeout(() => setState("idle"), 4000);
    },
    onError: () => {
      setState("error");
      setTimeout(() => setState("idle"), 4000);
    },
  });

  return (
    <Button
      variant={state === "idle" ? "secondary" : "quiet"}
      disabled={mutation.isPending || state !== "idle"}
      title="Flashes on the gateway's next poll"
      onClick={() => mutation.mutate()}
      className={clsx(
        state === "queued" && "text-ok",
        state === "error" && "text-bad",
      )}
    >
      <Lightbulb size={15} aria-hidden="true" />
      {mutation.isPending
        ? "…"
        : state === "queued"
          ? "Queued"
          : state === "error"
            ? "Failed"
            : "Flash it"}
    </Button>
  );
}

/** Everything the old table had a column for, kept one keystroke away rather
 * than on screen at all times. */
function RawFields({ tag }: { tag: Tag }) {
  const rows: [string, string][] = [
    ["Signal", tag.signal ?? "—"],
    ["RF power", tag.rf_power != null ? `${tag.rf_power} dBm` : "—"],
    [
      "Battery",
      tag.battery_volts != null
        ? `${tag.battery_volts.toFixed(1)} V${tag.low_battery ? " (low)" : ""}`
        : "—",
    ],
    ["Last heard", tag.last_seen_at ? relativeTime(tag.last_seen_at) : "never"],
    [
      "Last confirmed",
      tag.last_confirmed_at
        ? new Date(tag.last_confirmed_at).toLocaleString()
        : "never",
    ],
    [
      "Latest update",
      tag.latest_update
        ? `${tag.latest_update.action} · ${tag.latest_update.status}${
            tag.latest_update.failure_code ? ` (${tag.latest_update.failure_code})` : ""
          }`
        : "none sent",
    ],
    ["Version", String(tag.version ?? 0)],
    ["Pricing", tag.pricing_type ?? "—"],
    ["Content hash", tag.content_hash ?? "—"],
    ["Variation", tag.variation_id ?? "unassigned"],
  ];

  return (
    <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-2.5 border-t border-line-soft pt-4 sm:grid-cols-2 lg:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-faint">{label}</dt>
          <dd className="truncate font-mono text-xs text-muted" title={value}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function LabelRow({
  tag,
  dimmed,
  onAssign,
}: {
  tag: Tag;
  dimmed?: boolean;
  onAssign: (tag: Tag) => void;
}) {
  const [open, setOpen] = useState(false);
  const status = tagStatus(tag);
  const variation = tagVariationLabel(tag);
  const storeName = useStoreNames();
  const panelId = `tag-${tag.id}-detail`;

  return (
    <li
      className={clsx(
        "rounded-xl border bg-surface p-4",
        // A colored edge on the rows that need something, so the group reads as
        // a group even once its heading has scrolled away.
        dimmed ? "border-line-soft" : clsx("border-line", toneEdge(status.tone)),
      )}
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3.5">
        <div className="min-w-0 flex-1 basis-60">
          <p className="text-[15px] font-semibold text-ink">
            {tag.name ?? <span className="text-muted">No product assigned</span>}
            {variation && (
              <span className="font-normal text-muted"> {variation}</span>
            )}
          </p>
          <p className="mt-1 text-xs text-faint">
            <span className="font-mono">{tag.id}</span> · {storeName(tag.store_id)}
          </p>
        </div>

        <div className="basis-48">
          <StatusLabel tone={status.tone}>{status.label}</StatusLabel>
          {status.detail && (
            <p className="mt-1 text-xs text-faint">{status.detail}</p>
          )}
        </div>

        <p className="shrink-0 basis-24 text-right font-mono text-[17px] text-ink tabular-nums">
          {tag.variation_id ? formatPrice(tag.price) : "—"}
        </p>

        <div className="flex flex-wrap items-center gap-2">
          {!dimmed && <LocateButton tagId={tag.id} />}
          <Button
            variant={tag.variation_id ? "secondary" : "primary"}
            onClick={() => onAssign(tag)}
          >
            {tag.variation_id ? "Reassign" : "Assign"}
          </Button>
          <Button
            variant="quiet"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((v) => !v)}
          >
            Details
            <ChevronDown
              size={15}
              aria-hidden="true"
              className={clsx("transition-transform", open && "rotate-180")}
            />
          </Button>
        </div>
      </div>

      {open && (
        <div id={panelId}>
          <RawFields tag={tag} />
        </div>
      )}
    </li>
  );
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "warn" | "ok" | "accent";
}) {
  const EDGE = {
    warn: "bg-warn-dot",
    ok: "bg-ok",
    accent: "bg-accent",
  } as const;
  const TEXT = { warn: "text-warn", ok: "text-ok", accent: "text-ink" } as const;

  return (
    <div className="flex min-w-0 flex-1 basis-44 overflow-hidden rounded-xl border border-line bg-surface">
      <span className={clsx("w-1 shrink-0", EDGE[tone])} aria-hidden="true" />
      <div className="px-4 py-3.5">
        <p className="text-xs font-medium text-muted">{label}</p>
        <p className={clsx("font-mono text-2xl tabular-nums", TEXT[tone])}>
          {value.toLocaleString("en-US")}
        </p>
      </div>
    </div>
  );
}

export function LabelList({
  onAssign,
  onAddTag,
}: {
  onAssign: (tag: Tag) => void;
  onAddTag: () => void;
}) {
  const { data, isLoading, error, dataUpdatedAt } = useTags();
  const { data: storeData } = useStores();
  const [search, setSearch] = useState("");
  const [store, setStore] = useState("");
  const [showAllWorking, setShowAllWorking] = useState(false);

  const stores = storeData ?? [];

  const { attention, watch, working, counts } = useMemo(() => {
    const tags = data ?? [];
    const q = search.trim().toLowerCase();

    const matches = tags.filter((t) => {
      if (store && t.store_id !== store) return false;
      if (!q) return true;
      return (
        t.id.toLowerCase().includes(q) ||
        (t.name ?? "").toLowerCase().includes(q) ||
        (t.variation_name ?? "").toLowerCase().includes(q) ||
        (t.sku ?? "").toLowerCase().includes(q)
      );
    });

    // Battery ascending stays the within-group order: the recurring task this
    // screen exists for is "which labels need batteries". Null volts sort last
    // — nothing is known about them to act on.
    const byBattery = (a: Tag, b: Tag) =>
      (a.battery_volts ?? Infinity) - (b.battery_volts ?? Infinity);

    const inGroup = (group: ReturnType<typeof tagStatus>["group"]) =>
      matches.filter((t) => tagStatus(t).group === group);

    const needs = inGroup("attention").sort(
      (a, b) => tagStatus(a).rank - tagStatus(b).rank || byBattery(a, b),
    );

    // Weakest first: the ones closest to dropping off, and the ones that say
    // where an access point would help.
    const weak = inGroup("watch").sort(
      (a, b) => (a.rf_power ?? 0) - (b.rf_power ?? 0) || byBattery(a, b),
    );

    const fine = inGroup("ok").sort(byBattery);

    return {
      attention: needs,
      watch: weak,
      working: fine,
      counts: {
        attention: needs.length,
        watch: weak.length,
        working: fine.length,
        total: matches.length,
      },
    };
  }, [data, search, store]);

  if (isLoading) return <Loading what="labels" />;
  if (error) return <ErrorState what="labels" error={error} />;

  const visibleWorking = showAllWorking ? working : working.slice(0, 8);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end gap-4">
        <div className="min-w-0 flex-1 basis-72">
          <h1 className="text-3xl font-bold tracking-tight text-ink">
            Shelf labels
          </h1>
          <p className="mt-1.5 text-[15px] text-muted">
            {counts.total.toLocaleString("en-US")}{" "}
            {counts.total === 1 ? "label" : "labels"} across{" "}
            {stores.length || "—"} {stores.length === 1 ? "store" : "stores"}.
            {counts.attention > 0 && (
              <span className="text-warn">
                {" "}
                {counts.attention} need{counts.attention === 1 ? "s" : ""} attention.
              </span>
            )}
          </p>
        </div>
        <Button variant="primary" className="min-h-11 px-5" onClick={onAddTag}>
          Add a label
        </Button>
      </div>

      <div className="mb-5 flex flex-wrap gap-2.5">
        <Tile label="Need attention" value={counts.attention} tone="warn" />
        <Tile label="Weak signal" value={counts.watch} tone="accent" />
        <Tile label="Working normally" value={counts.working} tone="ok" />
        <Tile label="Labels in total" value={counts.total} tone="accent" />
      </div>

      <div className="mb-8 flex flex-wrap items-center gap-2.5">
        <SearchInput
          label="Search labels"
          placeholder="Search a product name, tag ID, or SKU"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="min-w-0 flex-1 basis-72"
        />
        <Select
          aria-label="Filter by store"
          value={store}
          onChange={(e) => setStore(e.target.value)}
          className="min-h-13 w-auto basis-44"
        >
          <option value="">All stores</option>
          {stores.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
      </div>

      {counts.total === 0 && (
        <Empty>
          {search || store
            ? "No labels match that."
            : "No labels yet — add one to get started."}
        </Empty>
      )}

      {attention.length > 0 && (
        <>
          <div className="mb-3 flex items-center gap-2.5">
            <h2 className="text-xs font-bold tracking-wider text-muted uppercase">
              Needs attention
            </h2>
            <Count tone="warn">{attention.length}</Count>
          </div>
          <ul className="mb-9 flex list-none flex-col gap-2.5 p-0">
            {attention.map((tag) => (
              <LabelRow key={tag.id} tag={tag} onAssign={onAssign} />
            ))}
          </ul>
        </>
      )}

      {watch.length > 0 && (
        <>
          <div className="mb-3 flex items-center gap-2.5">
            <h2 className="text-xs font-bold tracking-wider text-muted uppercase">
              Weak signal · still updating
            </h2>
            <Count tone="warn">{watch.length}</Count>
          </div>
          <ul className="mb-9 flex list-none flex-col gap-2.5 p-0">
            {watch.map((tag) => (
              <LabelRow key={tag.id} tag={tag} onAssign={onAssign} />
            ))}
          </ul>
        </>
      )}

      {working.length > 0 && (
        <>
          <div className="mb-3 flex items-center gap-2.5">
            <h2 className="text-xs font-bold tracking-wider text-muted uppercase">
              Working normally
            </h2>
            <Count tone="ok">{working.length}</Count>
          </div>
          <ul className="flex list-none flex-col gap-2 p-0">
            {visibleWorking.map((tag) => (
              <LabelRow key={tag.id} tag={tag} dimmed onAssign={onAssign} />
            ))}
          </ul>
          {working.length > visibleWorking.length && (
            <Button
              className="mt-3 w-full border border-dashed"
              onClick={() => setShowAllWorking(true)}
            >
              Show the other {(working.length - visibleWorking.length).toLocaleString("en-US")}
            </Button>
          )}
        </>
      )}

      <p className="mt-8 text-center text-xs text-faint">
        Updated {relativeTime(new Date(dataUpdatedAt).toISOString())}
      </p>
    </div>
  );
}
