import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";
import { api } from "./api/client";
import type { components } from "./api/schema.d.ts";
import { signOut } from "./auth/userManager";
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
      if (!/^[0-9A-F]{16}$/.test(id)) {
        throw new Error("Tag ID must be exactly 16 uppercase hex characters");
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
              Tag ID (16-char hex)
            </label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono uppercase focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="AABBCCDD11223344"
              value={tagId}
              maxLength={16}
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
    { id: "battery_pct", desc: false },
  ]);

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
    columnHelper.accessor("battery_pct", {
      header: "Battery",
      cell: (info) => {
        const pct = info.getValue();
        if (pct == null) return <span className="text-gray-400">—</span>;
        const color =
          pct <= 20
            ? "text-red-600"
            : pct <= 50
              ? "text-yellow-600"
              : "text-green-600";
        return <span className={color}>{pct}%</span>;
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

export default function App() {
  const [modal, setModal] = useState<
    { tagId: string; variationId?: string | null } | "new" | null
  >(null);
  const [tab, setTab] = useState<"tags" | "staff">("tags");

  const { data: me, error: meError } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error } = await api.GET("/me");
      if (error) throw error;
      return data;
    },
    retry: false,
  });

  const tabClass = (t: typeof tab) =>
    `px-3 py-1.5 text-sm rounded ${tab === t ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"}`;

  if (meError) {
    const detail =
      typeof meError === "object" && "detail" in meError && typeof meError.detail === "string"
        ? meError.detail
        : null;
    return (
      <div className="p-6 space-y-3">
        <p className="text-sm text-red-600">
          {detail ?? "Your account doesn't have access to this dashboard."}
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

      <main className="p-6">
        {tab === "tags" ? (
          <TagGrid
            onAssign={(tag) =>
              setModal({ tagId: tag.id, variationId: tag.variation_id })
            }
          />
        ) : (
          <StaffPanel />
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
