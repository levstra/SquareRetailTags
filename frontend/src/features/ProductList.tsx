import { Info } from "lucide-react";
import { useMemo, useState } from "react";
import { formatPrice, variationLabel } from "../lib/format";
import { useTags, useVariations, type Variation } from "../lib/queries";
import {
  Button,
  Empty,
  ErrorState,
  Loading,
  SearchInput,
} from "../ui";

export function ProductList({
  onSelect,
}: {
  onSelect: (variation: Variation) => void;
}) {
  const { data: varData, isLoading, error } = useVariations();
  const { data: tagData } = useTags();
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(30);

  const labelCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const tag of tagData ?? []) {
      if (!tag.variation_id) continue;
      counts.set(tag.variation_id, (counts.get(tag.variation_id) ?? 0) + 1);
    }
    return counts;
  }, [tagData]);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    const all = varData ?? [];
    if (!q) return all;
    return all.filter(
      (v) =>
        v.name.toLowerCase().includes(q) ||
        v.variation_name.toLowerCase().includes(q) ||
        (v.sku ?? "").toLowerCase().includes(q),
    );
  }, [varData, search]);

  if (isLoading) return <Loading what="products" />;
  if (error) return <ErrorState what="products" error={error} />;

  const visible = matches.slice(0, limit);

  return (
    <div>
      <h1 className="text-3xl font-bold tracking-tight text-ink">Products</h1>
      <p className="mt-1.5 text-[15px] text-muted">
        Straight from Square. Pick one to put it on a label.
      </p>

      <div className="mt-6 mb-7 flex items-start gap-2.5 rounded-xl border border-line bg-surface px-4 py-3">
        <Info size={16} className="mt-0.5 shrink-0 text-faint" aria-hidden="true" />
        <p className="text-[13px] text-muted">
          Prices are mirrored from Square and can’t be edited here. Change a
          price in Square and the next sync republishes the labels that show it.
        </p>
      </div>

      <SearchInput
        label="Search products"
        placeholder="Search a product name, variation, or SKU"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-7"
      />

      {matches.length === 0 && (
        <Empty>
          {search ? "No products match that." : "No products synced yet."}
        </Empty>
      )}

      <ul className="flex list-none flex-col gap-2 p-0">
        {visible.map((v) => {
          const count = labelCounts.get(v.id) ?? 0;
          const variation = variationLabel(v);
          return (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => onSelect(v)}
                className="w-full rounded-xl border border-line-soft bg-surface p-4 text-left transition-colors hover:border-accent-line"
              >
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5">
                  <div className="min-w-0 flex-1 basis-60">
                    <p className="text-[15px] font-medium text-ink">
                      {v.name}
                      {variation && (
                        <span className="font-normal text-muted"> {variation}</span>
                      )}
                    </p>
                    {v.sku && (
                      <p className="mt-1 font-mono text-xs text-faint">{v.sku}</p>
                    )}
                  </div>

                  <p className="basis-32 text-[13px] text-muted">
                    {count === 0 ? (
                      <span className="text-faint">no labels yet</span>
                    ) : (
                      `on ${count} label${count === 1 ? "" : "s"}`
                    )}
                  </p>

                  <p className="shrink-0 basis-28 text-right text-ink">
                    {v.pricing_type === "VARIABLE_PRICING" ? (
                      <span className="text-[13px] text-warn">Priced at register</span>
                    ) : (
                      <span className="font-mono text-base tabular-nums">
                        {formatPrice(v.price)}
                      </span>
                    )}
                  </p>
                </div>
              </button>
            </li>
          );
        })}
      </ul>

      {matches.length > visible.length && (
        <Button
          className="mt-3 w-full border border-dashed"
          onClick={() => setLimit((n) => n + 60)}
        >
          Show more ({(matches.length - visible.length).toLocaleString("en-US")} left)
        </Button>
      )}
    </div>
  );
}
