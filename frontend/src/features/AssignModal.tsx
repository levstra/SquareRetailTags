import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { formatPrice, variationLabel } from "../lib/format";
import {
  TAG_ID_RE,
  errorMessage,
  useStores,
  useVariations,
  type Variation,
} from "../lib/queries";
import { Button, Field, Modal, Select, TextInput } from "../ui";

export type AssignTarget = {
  tagId: string;
  storeId?: string;
  variationId?: string | null;
};

export function AssignModal({
  initial,
  onClose,
}: {
  initial?: AssignTarget;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [tagId, setTagId] = useState(initial?.tagId ?? "");
  const [storeId, setStoreId] = useState(initial?.storeId ?? "");
  const [variationId, setVariationId] = useState(initial?.variationId ?? "");
  const [error, setError] = useState("");

  const { data: varData } = useVariations();
  const { data: storeData } = useStores();
  // Most users work in one store; don't make them pick it.
  const onlyStore = storeData?.length === 1 ? storeData[0].id : "";
  const effectiveStoreId = storeId || onlyStore;

  const mutation = useMutation({
    mutationFn: async () => {
      const id = tagId.trim().toUpperCase();
      if (!TAG_ID_RE.test(id)) {
        throw new Error("Tag ID must be exactly 12 uppercase hex characters");
      }
      if (!effectiveStoreId) {
        throw new Error("Store is required");
      }
      const { error, response } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: id } },
        body: { store_id: effectiveStoreId, variation_id: variationId || null },
      });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      onClose();
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const variations = varData ?? [];
  const stores = storeData ?? [];

  function optionLabel(v: Variation) {
    const variation = variationLabel(v);
    return `${v.name}${variation ? ` › ${variation}` : ""} — ${formatPrice(v.price)}`;
  }

  return (
    <Modal
      title={initial?.tagId ? "Change what this label shows" : "Add a label"}
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Tag ID" hint="12 hex characters, as printed on the label">
          <TextInput
            placeholder="AABBCCDD1122"
            className="font-mono uppercase"
            value={tagId}
            maxLength={12}
            readOnly={!!initial?.tagId}
            autoFocus={!initial?.tagId}
            onChange={(e) => setTagId(e.target.value.toUpperCase())}
          />
        </Field>

        <Field label="Store">
          <Select
            value={effectiveStoreId}
            disabled={!!initial?.storeId}
            onChange={(e) => setStoreId(e.target.value)}
          >
            <option value="">— Select a store —</option>
            {stores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="Product"
          hint="Leave unassigned and the label shows its blank screen"
        >
          <Select
            value={variationId}
            onChange={(e) => setVariationId(e.target.value)}
          >
            <option value="">— Unassigned —</option>
            {variations.map((v) => (
              <option key={v.id} value={v.id}>
                {optionLabel(v)}
              </option>
            ))}
          </Select>
        </Field>

        {error && (
          <p className="rounded-lg bg-bad-tint px-3 py-2 text-sm text-bad">{error}</p>
        )}
      </div>
    </Modal>
  );
}
