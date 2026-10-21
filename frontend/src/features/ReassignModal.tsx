import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { api, ApiError } from "../api/client";
import type { components } from "../api/schema.d.ts";
import { formatPrice, tagVariationLabel } from "../lib/format";
import { errorMessage, useStoreNames, type Tag } from "../lib/queries";
import { Button, Modal, SearchInput } from "../ui";

// Barcode lookups return VariationWithStorePrice; name search returns Variation.
type Choice =
  | components["schemas"]["VariationWithStorePrice"]
  | components["schemas"]["Variation"];

/** What Save will make the label show: a product, or its unassigned screen. */
type Target = { kind: "product"; product: Choice } | { kind: "unassign" };

function choiceLabel(v: Choice): string {
  return v.variation_name && v.variation_name !== "Regular"
    ? `${v.name} · ${v.variation_name}`
    : v.name;
}

/** Why a product can't go on a label, or null when it can. The server refuses
 * a label without a fixed price; say so before the user hits Save. */
function unpairable(v: Choice): string | null {
  if (v.pricing_type === "VARIABLE_PRICING") return "Variable-price items can't be shown on a label.";
  if (v.price == null) return "This item has no price, so it can't be shown on a label.";
  return null;
}

/** The value once it has stopped changing for `ms`, so typing doesn't fire a
 * search per keystroke. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/** Reassign or unassign a label, laid out like the Pair page: what it shows
 * now beside what it will show, and a search (or scanned barcode) to pick the
 * new product instead of one long dropdown. Only assigned labels open this —
 * unassigned ones aren't on the Labels list. */
export function ReassignModal({ tag, onClose }: { tag: Tag; onClose: () => void }) {
  const queryClient = useQueryClient();
  const storeName = useStoreNames();
  const storeId = tag.store_id ?? null;
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState<Target | null>(null);
  // Several products sharing one barcode: let the user pick.
  const [choices, setChoices] = useState<Choice[]>([]);
  const [notFound, setNotFound] = useState<string | null>(null);
  const [error, setError] = useState("");

  const q = search.trim();
  const debouncedQ = useDebounced(q, 250);
  const results = useQuery({
    queryKey: ["variation-search", storeId, debouncedQ],
    enabled: debouncedQ.length >= 2,
    // Keep showing the last results while the next ones load, rather than
    // blanking the list on every keystroke.
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/catalog/variations", {
        params: { query: { q: debouncedQ, ...(storeId ? { store_id: storeId } : {}) } },
      });
      if (!data) throw new ApiError(response, error);
      return data.variations;
    },
  });

  // Enter in the search box: a handheld scanner types the barcode and hits
  // Enter, so try it as a product code.
  const lookup = useMutation({
    mutationFn: async (code: string) => {
      const { data, error, response } = await api.GET("/catalog/lookup", {
        params: { query: { code, store_id: storeId! } },
      });
      if (!data) throw new ApiError(response, error);
      return data.matches;
    },
    onSuccess: (matches, code) => {
      if (matches.length === 0) {
        setNotFound(code);
        return;
      }
      setSearch("");
      setNotFound(null);
      if (matches.length === 1) {
        setTarget({ kind: "product", product: matches[0] });
        setChoices([]);
      } else {
        setChoices(matches);
      }
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const save = useMutation({
    mutationFn: async (t: Target) => {
      const { error, response } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: tag.id } },
        body: {
          store_id: storeId,
          variation_id: t.kind === "product" ? t.product.id : null,
        },
      });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      onClose();
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const pick = (product: Choice) => {
    setTarget({ kind: "product", product });
    setChoices([]);
    setNotFound(null);
    setSearch("");
    setError("");
  };

  const blocked = target?.kind === "product" ? unpairable(target.product) : null;
  const unchanged =
    target === null || (target.kind === "product" && target.product.id === tag.variation_id);
  const currentVariation = tagVariationLabel(tag);
  const list = choices.length > 0 ? choices : q.length >= 2 ? (results.data ?? []) : [];

  return (
    <Modal
      wide
      title="Reassign this label"
      onClose={onClose}
      footer={
        <>
          <Button
            variant="quiet"
            className="mr-auto text-bad hover:text-bad"
            disabled={target?.kind === "unassign"}
            onClick={() => {
              setTarget({ kind: "unassign" });
              setChoices([]);
              setError("");
            }}
          >
            Unassign
          </Button>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={unchanged || !!blocked || save.isPending}
            onClick={() => target && save.mutate(target)}
          >
            {save.isPending
              ? "Saving…"
              : target?.kind === "unassign"
                ? "Unassign label"
                : "Save"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="-mt-3 text-xs text-faint">
          <span className="font-mono">{tag.id}</span> · {storeName(tag.store_id)}
        </p>

        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="min-w-0 rounded-xl border border-line bg-surface p-4">
            <h3 className="mb-1.5 text-xs font-bold tracking-wider text-muted uppercase">
              Showing now
            </h3>
            <p className="font-medium text-ink">{tag.name}</p>
            {currentVariation && <p className="text-sm text-muted">{currentVariation}</p>}
            <p className="font-mono text-lg text-ink tabular-nums">{formatPrice(tag.price)}</p>
          </div>

          <div
            className={clsx(
              "min-w-0 rounded-xl border-2 p-4",
              target ? "border-accent bg-accent-tint" : "border-dashed border-line bg-surface",
            )}
          >
            <h3 className="mb-1.5 text-xs font-bold tracking-wider text-muted uppercase">
              Will show
            </h3>
            {target?.kind === "product" ? (
              <>
                <p className="font-medium text-ink">{target.product.name}</p>
                {target.product.variation_name !== "Regular" && (
                  <p className="text-sm text-muted">{target.product.variation_name}</p>
                )}
                <p className="font-mono text-lg text-ink tabular-nums">
                  {formatPrice(target.product.price)}
                </p>
                {blocked && <p className="text-sm text-bad">{blocked}</p>}
              </>
            ) : target?.kind === "unassign" ? (
              <>
                <p className="font-medium text-ink">Nothing — unassigned</p>
                <p className="text-sm text-muted">
                  It leaves the Labels list. Scan it on Pair tags to use it again.
                </p>
              </>
            ) : (
              <p className="text-sm text-faint">Search or scan a product below</p>
            )}
          </div>
        </section>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (q && storeId) lookup.mutate(q);
          }}
        >
          <SearchInput
            label="Search products"
            placeholder="Search by name or SKU, or scan a barcode"
            autoFocus
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setChoices([]);
              setNotFound(null);
            }}
          />
        </form>

        {/* Fixed height, so the dialog doesn't grow and shrink (and, being
            centered, jump) as results come and go. */}
        <div className="h-72 overflow-y-auto">
          {notFound ? (
            <p className="break-words text-sm text-bad">
              No product with code {notFound} at this store.
            </p>
          ) : choices.length > 0 ? (
            <p className="mb-2 text-sm text-muted">Several products share that barcode — pick one.</p>
          ) : results.isError && q.length >= 2 ? (
            <p className="text-sm text-bad">{errorMessage(results.error)}</p>
          ) : q.length < 2 ? (
            <p className="text-sm text-faint">Type at least two letters to search.</p>
          ) : (
            list.length === 0 &&
            results.isSuccess &&
            !results.isPlaceholderData &&
            debouncedQ === q && <p className="text-sm text-faint">No products match “{q}”.</p>
          )}

          {list.length > 0 && !notFound && (
            <ul
              className={clsx(
                "flex list-none flex-col gap-1.5 p-0 transition-opacity",
                // Dim, don't blank, results that are about to be replaced.
                choices.length === 0 && (results.isPlaceholderData || debouncedQ !== q) && "opacity-60",
              )}
            >
              {list.map((v) => (
                <li key={v.id}>
                  <button
                    type="button"
                    className={clsx(
                      "flex min-h-10 w-full items-center justify-between gap-3 rounded-lg border bg-surface px-3 py-1.5 text-left text-sm text-ink hover:border-accent-line",
                      v.id === tag.variation_id ? "border-accent-line" : "border-line-soft",
                    )}
                    onClick={() => pick(v)}
                  >
                    <span className="min-w-0 truncate">
                      {choiceLabel(v)}
                      {v.sku && <span className="ml-2 font-mono text-xs text-faint">{v.sku}</span>}
                    </span>
                    <span className="shrink-0 font-mono tabular-nums">{formatPrice(v.price)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && (
          <p className="rounded-lg bg-bad-tint px-3 py-2 text-sm text-bad">{error}</p>
        )}
      </div>
    </Modal>
  );
}
