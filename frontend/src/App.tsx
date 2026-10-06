import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { useEffect, useState } from "react";
import { api, ApiError, clearRedirectMarker } from "./api/client";
import type { components } from "./api/schema.d.ts";
import { signOut } from "./auth/userManager";
import { SquareSettings } from "./square/SquareSettings";
import { squareConnectionQuery } from "./square/queries";
import { StaffPanel } from "./staff/StaffPanel";

type Tag = components["schemas"]["Tag"];
type Variation = components["schemas"]["Variation"];

// ── Assign Modal ────────────────────────────────────────────────────────────

function AssignModal({
  initial,
  onClose,
}: {
  initial?: { tagId: string; variationId?: string | null };
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [tagId, setTagId] = useState(initial?.tagId ?? "");
  const [variationId, setVariationId] = useState(initial?.variationId ?? "");
  const [error, setError] = useState("");

  const { data: varData } = useQuery({
    queryKey: ["variations"],
    queryFn: async () => {
      const { data, error } = await api.GET("/catalog/variations", {});
      if (error) throw error;
      return (data as { variations: Variation[] }).variations;
    },
  });

  const mutation = useMutation({
    mutationFn: async () => {
      const id = tagId.trim().toUpperCase();
      if (!/^[0-9A-F]{12}$/.test(id)) {
        throw new Error("Tag ID must be exactly 12 hex characters");
      }
      const { error } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: id } },
        body: { variation_id: variationId || null },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const variations = varData ?? [];

  function formatVariationLabel(v: Variation) {
    const price =
      v.price != null ? ` — $${(v.price / 100).toFixed(2)}` : " — Variable";
    return `${v.name} › ${v.variation_name}${price}`;
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-base font-semibold text-gray-900 mb-4">
          {initial?.tagId ? "Edit Tag Assignment" : "Add Tag"}
        </h2>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Tag ID (12-char hex)
            </label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono uppercase focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="840001282A7E"
              value={tagId}
              maxLength={12}
              readOnly={!!initial?.tagId}
              onChange={(e) => setTagId(e.target.value.toUpperCase())}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Variation
            </label>
            <select
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={variationId}
              onChange={(e) => setVariationId(e.target.value)}
            >
              <option value="">— Unassigned —</option>
              {variations.map((v) => (
                <option key={v.id} value={v.id}>
                  {formatVariationLabel(v)}
                </option>
              ))}
            </select>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Tag Grid ─────────────────────────────────────────────────────────────────

const columnHelper = createColumnHelper<Tag>();

function TagGrid({ onAssign }: { onAssign: (tag: Tag) => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["tags"],
    queryFn: async () => {
      const { data, error } = await api.GET("/tags", {});
      if (error) throw error;
      return (data as { tags: Tag[] }).tags;
    },
    refetchInterval: 5_000,
  });

  const [sorting, setSorting] = useState<SortingState>([
    { id: "attention", desc: true },
  ]);

  // Higher = needs attention sooner: failed command, low battery, not heard from, still sending.
  function attention(tag: Tag): number {
    let score = 0;
    if (tag.latest_update?.status === "Failed") score += 8;
    if (tag.low_battery) score += 4;
    if (tag.last_seen_at == null || Date.now() - new Date(tag.last_seen_at).getTime() > 6 * 3600_000) score += 2;
    if (tag.latest_update && ["Pending", "Dispatched"].includes(tag.latest_update.status)) score += 1;
    return score;
  }

  const columns = [
    columnHelper.accessor("id", {
      header: "Tag ID",
      cell: (info) => (
        <span className="font-mono text-xs">{info.getValue()}</span>
      ),
    }),
    columnHelper.accessor("name", {
      header: "Product",
      cell: (info) =>
        info.getValue() ?? <span className="text-gray-400">—</span>,
    }),
    columnHelper.accessor("variation_name", {
      header: "Variation",
      cell: (info) =>
        info.getValue() ?? <span className="text-gray-400">—</span>,
    }),
    columnHelper.accessor("price", {
      header: "Price",
      cell: (info) => {
        const cents = info.getValue();
        if (cents == null)
          return <span className="text-gray-400">Variable</span>;
        return `$${(cents / 100).toFixed(2)}`;
      },
    }),
    columnHelper.accessor((t) => attention(t), {
      id: "attention",
      header: "Status",
      cell: (info) => {
        const tag = info.row.original;
        const u = tag.latest_update;
        if (u?.status === "Failed")
          return <span className="text-red-600">Failed{u.failure_code ? ` (${u.failure_code})` : ""}</span>;
        if (u && (u.status === "Pending" || u.status === "Dispatched"))
          return <span className="text-yellow-600">Sending…</span>;
        if (tag.pricing_type === "VARIABLE_PRICING")
          return <span className="text-yellow-600">Variable price — shows unassigned</span>;
        // A Succeeded Unassign means the tag was blanked, not that it shows a price.
        if (u?.status === "Succeeded" && u.action === "Unassign")
          return <span className="text-gray-500">Unassigned</span>;
        if (u?.status === "Succeeded") return <span className="text-green-600">On shelf</span>;
        return <span className="text-gray-400">—</span>;
      },
    }),
    columnHelper.accessor("low_battery", {
      header: "Battery",
      cell: (info) => {
        const low = info.getValue();
        if (low == null) return <span className="text-gray-400">—</span>;
        return low ? <span className="text-red-600">Low</span> : <span className="text-green-600">OK</span>;
      },
    }),
    columnHelper.accessor("last_seen_at", {
      header: "Last seen",
      cell: (info) => {
        const v = info.getValue();
        return v ? new Date(v).toLocaleString() : <span className="text-gray-400">Never</span>;
      },
    }),
    columnHelper.accessor("last_confirmed_at", {
      header: "Last Confirmed",
      cell: (info) => {
        const v = info.getValue();
        if (!v) return <span className="text-gray-400">Never</span>;
        return new Date(v).toLocaleString();
      },
    }),
    columnHelper.accessor("content_hash", {
      header: "Hash",
      cell: (info) => {
        const h = info.getValue();
        if (!h) return <span className="text-gray-400">—</span>;
        return <span className="font-mono text-xs">{h.slice(0, 8)}…</span>;
      },
    }),
    columnHelper.display({
      id: "actions",
      header: "",
      cell: (info) => (
        <button
          className="text-xs text-blue-600 hover:underline"
          onClick={() => onAssign(info.row.original)}
        >
          Assign
        </button>
      ),
    }),
  ];

  const table = useReactTable({
    data: data ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (isLoading)
    return <div className="p-8 text-center text-gray-500">Loading tags…</div>;
  if (error)
    return (
      <div className="p-8 text-center text-red-600">
        Failed to load tags: {String(error)}
      </div>
    );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-gray-200 bg-gray-50">
              {hg.headers.map((header) => (
                <th
                  key={header.id}
                  className="px-4 py-2 text-left font-medium text-gray-600 cursor-pointer select-none"
                  onClick={header.column.getToggleSortingHandler()}
                >
                  {flexRender(
                    header.column.columnDef.header,
                    header.getContext(),
                  )}
                  {header.column.getIsSorted() === "asc"
                    ? " ↑"
                    : header.column.getIsSorted() === "desc"
                      ? " ↓"
                      : ""}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-4 py-8 text-center text-gray-400"
              >
                No tags yet. Click "Add Tag" to register one.
              </td>
            </tr>
          ) : (
            table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-gray-100 hover:bg-gray-50"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

type Tab = "tags" | "staff" | "square";

// Square's OAuth callback lands on /settings/square?result=...; read it once.
const returnedFromSquare = window.location.pathname === "/settings/square";
const squareResult = returnedFromSquare ? new URLSearchParams(window.location.search).get("result") : null;

export default function App() {
  const [modal, setModal] = useState<
    { tagId: string; variationId?: string | null } | "new" | null
  >(null);
  const [tab, setTab] = useState<Tab>(returnedFromSquare ? "square" : "tags");

  useEffect(() => {
    if (window.location.pathname !== "/") window.history.replaceState({}, document.title, "/");
  }, []);

  const { data: me, error: meError } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/me");
      if (!response.ok) throw new ApiError(response, error);
      clearRedirectMarker();
      return data;
    },
    retry: false,
  });

  const { data: square } = useQuery({ ...squareConnectionQuery, enabled: !!me });
  const squareNeedsAttention = square != null && square.status !== "active";

  const tabClass = (t: typeof tab) =>
    `px-3 py-1.5 text-sm rounded ${tab === t ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"}`;

  if (meError) {
    const detail = meError instanceof ApiError ? meError.detail : null;
    const meUnauthorized = meError instanceof ApiError && meError.status === 401;
    return (
      <div className="p-6 space-y-3">
        <p className="text-sm text-red-600">
          {meUnauthorized
            ? "We couldn't verify your sign-in. Try signing out and in again; if it keeps happening, sign-in may be misconfigured."
            : (detail ?? "Your account doesn't have access to this dashboard.")}
        </p>
        <button className="text-sm underline text-gray-600" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold text-gray-900">
            {me?.business.name ?? "ESL Pricing"}
          </h1>
          <nav className="flex gap-1">
            <button className={tabClass("tags")} onClick={() => setTab("tags")}>
              Tags
            </button>
            {me?.role === "owner" && (
              <button className={tabClass("staff")} onClick={() => setTab("staff")}>
                Staff
              </button>
            )}
            {me?.role === "owner" && (
              <button className={tabClass("square")} onClick={() => setTab("square")}>
                Square
              </button>
            )}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {tab === "tags" && (
            <button
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
              onClick={() => setModal("new")}
            >
              + Add Tag
            </button>
          )}
          <span className="text-sm text-gray-500">
            {me?.email} · {me?.role}
          </span>
          <button className="text-sm underline text-gray-600" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      {squareNeedsAttention && (
        <div className="border-b border-yellow-200 bg-yellow-50 px-6 py-2 text-sm text-yellow-900">
          {me?.role === "owner" ? (
            <>
              Prices aren't syncing from Square.{" "}
              <button className="underline" onClick={() => setTab("square")}>
                {square?.status === "not_connected" ? "Connect Square" : "Reconnect Square"}
              </button>
            </>
          ) : (
            "Prices aren't syncing from Square. Ask the business owner to reconnect Square."
          )}
        </div>
      )}

      <main className="p-6">
        {tab === "square" && !me ? (
          <div className="p-8 text-center text-gray-500">Loading…</div>
        ) : tab === "staff" && me?.role === "owner" ? (
          <StaffPanel />
        ) : tab === "square" && me?.role === "owner" ? (
          <SquareSettings result={squareResult} />
        ) : (
          <TagGrid
            onAssign={(tag) =>
              setModal({ tagId: tag.id, variationId: tag.variation_id })
            }
          />
        )}
      </main>

      {modal !== null && (
        <AssignModal
          initial={modal === "new" ? undefined : modal}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}
