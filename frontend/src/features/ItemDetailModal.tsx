import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import clsx from "clsx";
import { api, ApiError } from "../api/client";
import { formatPrice, tagStatus, variationLabel } from "../lib/format";
import {
  TAG_ID_RE,
  errorMessage,
  useStoreNames,
  useStores,
  useTags,
  type Tag,
  type Variation,
} from "../lib/queries";
import { Button, Modal, Select, StatusLabel, TextInput } from "../ui";

/** What this product resolves to in each store.
 *
 * Built from the labels themselves, because that is where a store-resolved
 * price actually exists: `GET /catalog/variations` returns the flat catalog
 * price only, so a store with no label for this product has no resolved price
 * to show and says so rather than repeating the catalog figure. */
function PriceByStore({
  variation,
  tags,
  stores,
}: {
  variation: Variation;
  tags: Tag[];
  stores: { id: string; name: string }[];
}) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {stores.map((store) => {
        const tag = tags.find((t) => t.store_id === store.id);
        const overridden =
          tag != null && variation.price != null && tag.price !== variation.price;

        return (
          <div
            key={store.id}
            className={clsx(
              "rounded-xl border px-3.5 py-3",
              overridden
                ? "border-accent-line bg-accent-tint"
                : "border-line-soft bg-surface-2",
            )}
          >
            <p
              className={clsx(
                "text-xs",
                overridden ? "text-accent-ink" : "text-muted",
              )}
            >
              {store.name}
            </p>
            {tag ? (
              <>
                <p className="font-mono text-xl text-ink tabular-nums">
                  {formatPrice(tag.price)}
                </p>
                <p
                  className={clsx(
                    "text-xs",
                    overridden ? "text-accent-ink" : "text-faint",
                  )}
                >
                  {overridden ? "Store override in Square" : "Square catalog price"}
                </p>
              </>
            ) : (
              <>
                <p className="text-xl text-faint">—</p>
                <p className="text-xs text-faint">No label here yet</p>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function ItemDetailModal({
  variation,
  onClose,
}: {
  variation: Variation;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: tagData } = useTags();
  const { data: storeData } = useStores();
  const [newTagId, setNewTagId] = useState("");
  const [newStoreId, setNewStoreId] = useState("");
  const [error, setError] = useState("");

  const storeName = useStoreNames();
  const tags = (tagData ?? []).filter((t) => t.variation_id === variation.id);
  const stores = storeData ?? [];
  const storeChoice = newStoreId || (stores.length === 1 ? stores[0].id : "");

  const invalidateTags = () =>
    queryClient.invalidateQueries({ queryKey: ["tags"] });

  const addMutation = useMutation({
    mutationFn: async () => {
      const id = newTagId.trim().toUpperCase();
      if (!TAG_ID_RE.test(id)) {
        throw new Error("Tag ID must be exactly 12 uppercase hex characters");
      }
      if (!storeChoice) {
        throw new Error("Store is required");
      }
      const { error, response } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: id } },
        body: { store_id: storeChoice, variation_id: variation.id },
      });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: () => {
      invalidateTags();
      setNewTagId("");
      setNewStoreId("");
      setError("");
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const unassignMutation = useMutation({
    mutationFn: async (tag: Tag) => {
      const { error, response } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: tag.id } },
        body: { store_id: tag.store_id ?? null, variation_id: null },
      });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: invalidateTags,
    onError: (e) => setError(errorMessage(e)),
  });

  const variationName = variationLabel(variation);

  return (
    <Modal
      title={variation.name}
      onClose={onClose}
      wide
      footer={
        <Button variant="quiet" onClick={onClose}>
          Close
        </Button>
      }
    >
      <p className="-mt-3 mb-6 text-sm text-muted">
        {variationName ? `${variationName} · ` : ""}
        {variation.pricing_type === "VARIABLE_PRICING"
          ? "Priced at the register"
          : formatPrice(variation.price)}
        {variation.sku ? ` · SKU ${variation.sku}` : ""}
      </p>

      <h3 className="mb-3 text-xs font-bold tracking-wider text-muted uppercase">
        What each store shows
      </h3>
      <div className="mb-7">
        <PriceByStore variation={variation} tags={tags} stores={stores} />
      </div>

      <h3 className="mb-3 text-xs font-bold tracking-wider text-muted uppercase">
        Labels showing this ({tags.length})
      </h3>
      <ul className="mb-7 flex max-h-56 list-none flex-col gap-0 overflow-y-auto p-0">
        {tags.length === 0 && (
          <li className="py-2 text-sm text-faint">
            None yet — add one below.
          </li>
        )}
        {tags.map((tag) => {
          const status = tagStatus(tag);
          return (
            <li
              key={tag.id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line-soft py-3 last:border-0"
            >
              <div className="min-w-0">
                <p className="font-mono text-xs text-ink">{tag.id}</p>
                <p className="mt-0.5 text-xs text-faint">{storeName(tag.store_id)}</p>
              </div>
              <StatusLabel tone={status.tone}>{status.label}</StatusLabel>
              <Button
                variant="quiet"
                className="text-bad"
                disabled={unassignMutation.isPending}
                onClick={() => unassignMutation.mutate(tag)}
              >
                Unassign
              </Button>
            </li>
          );
        })}
      </ul>

      <h3 className="mb-3 text-xs font-bold tracking-wider text-muted uppercase">
        Put this on another label
      </h3>
      <div className="flex flex-wrap items-start gap-2">
        <TextInput
          aria-label="Tag ID"
          placeholder="Tag ID (12-char hex)"
          className="min-w-0 flex-1 basis-44 font-mono uppercase"
          value={newTagId}
          maxLength={12}
          onChange={(e) => setNewTagId(e.target.value.toUpperCase())}
        />
        <Select
          aria-label="Store"
          className="w-auto basis-36"
          value={storeChoice}
          onChange={(e) => setNewStoreId(e.target.value)}
        >
          <option value="">— Store —</option>
          {stores.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
        <Button
          variant="primary"
          className="min-h-11"
          disabled={addMutation.isPending}
          onClick={() => addMutation.mutate()}
        >
          Add
        </Button>
      </div>
      {error && (
        <p className="mt-2.5 rounded-lg bg-bad-tint px-3 py-2 text-sm text-bad">
          {error}
        </p>
      )}
    </Modal>
  );
}
