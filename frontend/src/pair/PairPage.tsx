import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { components } from "../api/schema.d.ts";
import { navigate } from "../router";
import { CameraScanner } from "./CameraScanner";
import { beep, unlockAudioOnFirstGesture, vibrate } from "./feedback";
import { formatPrice } from "./format";
import { initialPairState, pairReducer, readyToPair, type ProductChoice, type TagSlot } from "./pairState";
import { statusMessage, type Tone } from "./statusMessages";
import { getLastStore, setLastStore } from "./storePref";
import { useWedge } from "./useWedge";

// Barcode lookups return VariationWithStorePrice; name search returns Variation.
type CatalogVariation = components["schemas"]["VariationWithStorePrice"] | components["schemas"]["Variation"];
type Summary = components["schemas"]["LabelUpdateSummary"];

const UNDO_MS = 8000;
const TONES: Record<Tone, string> = {
  pending: "bg-gray-100 text-gray-700",
  success: "bg-green-100 text-green-800",
  warning: "bg-amber-100 text-amber-800",
  error: "bg-red-100 text-red-700",
};

function toChoice(v: CatalogVariation): ProductChoice {
  return {
    id: v.id, name: v.name, variationName: v.variation_name, price: v.price ?? null,
    pricingType: v.pricing_type, currency: v.currency ?? null,
  };
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong";
}

function UpdateStatus({ initial }: { initial: Summary }) {
  const { data, isError, error } = useQuery({
    queryKey: ["label-update", initial.update_id],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/label-updates/{update_id}", {
        params: { path: { update_id: initial.update_id } },
      });
      if (!data) throw new ApiError(response, error);
      return data;
    },
    initialData: initial,
    // Stop on a final status, and on a persistent error rather than polling it forever.
    refetchInterval: (q) =>
      q.state.status === "error" || (q.state.data && statusMessage(q.state.data).final) ? false : 2000,
  });
  if (isError) {
    return <p className={`rounded px-3 py-2 text-sm ${TONES.error}`}>Couldn't check this label: {errorText(error)}</p>;
  }
  const m = statusMessage(data ?? initial);
  return <p className={`rounded px-3 py-2 text-sm ${TONES[m.tone]}`}>{m.text}</p>;
}

interface PairRequest {
  tag: TagSlot;
  product: ProductChoice;
  storeId: string;
}

interface LastPair {
  tagId: string;
  productLabel: string;
  // What undo puts back: the tag's variation and store before this pair.
  previousVariationId: string | null;
  previousName: string | null;
  previousStoreId: string | null;
  update: Summary;
}

export function PairPage() {
  const stores = useQuery({
    queryKey: ["stores"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/stores");
      if (!data) throw new ApiError(response, error);
      return data.stores;
    },
  });
  const [chosenStore, setChosenStore] = useState<string | null>(() => getLastStore());
  const storeList = stores.data ?? [];
  const storeId = storeList.find((s) => s.id === chosenStore)?.id ?? storeList[0]?.id ?? null;
  // Responses that started under another store (or for another tag) are dropped on arrival.
  const storeIdRef = useRef(storeId);
  useEffect(() => {
    storeIdRef.current = storeId;
  }, [storeId]);

  const [state, dispatch] = useReducer(pairReducer, initialPairState);
  // pairState treats "no matches" like "nothing scanned"; the page remembers the code to say so.
  const [notFound, setNotFound] = useState<string | null>(null);
  const [cameraOn, setCameraOn] = useState(true);
  const [cameraFailed, setCameraFailed] = useState(false);
  const [notice, setNotice] = useState<{ tone: Tone; text: string } | null>(null);
  const [flash, setFlash] = useState(false);
  const [lastPair, setLastPair] = useState<LastPair | null>(null);
  const [undoable, setUndoable] = useState(false);
  const [locate, setLocate] = useState<Summary | null>(null);
  const [manual, setManual] = useState("");
  const [search, setSearch] = useState("");

  const labels = useQuery({
    queryKey: ["store-labels", storeId],
    enabled: storeId !== null,
    staleTime: 60_000,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/stores/{store_id}/labels", {
        params: { path: { store_id: storeId! } },
      });
      if (!data) throw new ApiError(response, error);
      return data.labels;
    },
  });
  const tagIdRef = useRef<string | null>(null);
  useEffect(() => {
    tagIdRef.current = state.tag?.tagId ?? null;
  }, [state.tag]);

  useEffect(() => unlockAudioOnFirstGesture(), []);

  const heard = useMemo(
    () => (labels.data ? new Set(labels.data.map((l) => l.tag_id)) : null),
    [labels.data],
  );

  const results = useQuery({
    queryKey: ["variation-search", storeId, search.trim()],
    enabled: storeId !== null && search.trim().length >= 2,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/catalog/variations", {
        params: { query: { q: search.trim(), store_id: storeId! } },
      });
      if (!data) throw new ApiError(response, error);
      return data.variations;
    },
  });

  useEffect(() => {
    if (!undoable) return;
    const t = window.setTimeout(() => setUndoable(false), UNDO_MS);
    return () => window.clearTimeout(t);
  }, [undoable, lastPair]);

  const fail = useCallback((text: string) => {
    setNotice({ tone: "error", text });
    beep(false);
  }, []);

  const clearSlots = () => {
    dispatch({ type: "clear" });
    setNotFound(null);
    setLocate(null);
  };

  const loadTag = useCallback(async (tagId: string, store: string) => {
    const { data, response } = await api.GET("/tags/{tag_id}", { params: { path: { tag_id: tagId } } });
    if (store !== storeIdRef.current) return; // the store changed while this was in flight
    // Until the store's label list loads, don't warn about the gateway.
    const seenByGateway = heard === null || heard.has(tagId);
    const blank = { tagId, currentName: null, previousVariationId: null, previousStoreId: null, seenByGateway };
    let tag: TagSlot;
    if (response.status === 404) {
      tag = { ...blank, status: "new" };
    } else if (response.status === 403) {
      tag = { ...blank, status: "other-store" };
    } else if (data) {
      tag = {
        tagId, status: data.variation_id ? "assigned" : "ready", currentName: data.name ?? null,
        previousVariationId: data.variation_id ?? null, previousStoreId: data.store_id ?? null, seenByGateway,
      };
    } else {
      return fail("Couldn't look up that tag");
    }
    dispatch({ type: "tag-found", tag });
    setLocate(null);
    beep();
  }, [heard, fail]);

  const loadProduct = useCallback(async (code: string, store: string) => {
    const { data, error, response } = await api.GET("/catalog/lookup", { params: { query: { code, store_id: store } } });
    if (store !== storeIdRef.current) return; // priced for the old store: drop it
    if (!data) return fail(errorText(new ApiError(response, error)));
    dispatch({ type: "product-found", matches: data.matches.map(toChoice) });
    if (data.matches.length === 0) {
      setNotFound(code);
      setSearch("");
      beep(false);
      return;
    }
    setNotFound(null);
    beep();
  }, [fail]);

  const handleScan = useCallback(async (raw: string) => {
    setNotice(null);
    if (!storeId) return fail("Pick a store first");
    const store = storeId;
    try {
      const { data, error, response } = await api.GET("/scan/classify", {
        params: { query: { code: raw, store_id: store } },
      });
      if (store !== storeIdRef.current) return;
      if (!data) return fail(errorText(new ApiError(response, error)));
      if (data.kind === "tag") await loadTag(data.normalized, store);
      else await loadProduct(data.normalized, store);
    } catch (e) {
      fail(errorText(e)); // network failure
    }
  }, [storeId, loadTag, loadProduct, fail]);

  // The handheld scanner works whenever the camera doesn't: off, or failed to start.
  useWedge(!cameraOn || cameraFailed, handleScan);

  const pair = useMutation({
    mutationFn: async ({ tag, product, storeId }: PairRequest) => {
      const { data, error, response } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: tag.tagId } },
        body: { variation_id: product.id, store_id: storeId },
      });
      if (!data) throw new ApiError(response, error);
      return data;
    },
    onSuccess: (data, { tag, product }) => {
      beep();
      vibrate();
      setFlash(true);
      window.setTimeout(() => setFlash(false), 400);
      if (data.latest_update) {
        setLastPair({
          tagId: tag.tagId,
          productLabel: `${product.name} · ${formatPrice(product.price, product.currency)}`,
          previousVariationId: tag.previousVariationId,
          previousName: tag.currentName,
          previousStoreId: tag.previousStoreId,
          update: data.latest_update,
        });
        setUndoable(true);
      }
      clearSlots();
      void labels.refetch();
    },
    onError: (e) => fail(errorText(e)),
  });

  const undo = useMutation({
    mutationFn: async (p: LastPair) => {
      // A null store keeps the tag where it is (a new tag stays in the store it was paired in).
      const { data, error, response } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: p.tagId } },
        body: { variation_id: p.previousVariationId, store_id: p.previousStoreId },
      });
      if (!data) throw new ApiError(response, error);
      return data;
    },
    onSuccess: (data, p) => {
      setUndoable(false);
      setNotice({ tone: "success", text: `Undone: ${p.tagId}` });
      if (data.latest_update) {
        const restored = p.previousVariationId ? `back to ${p.previousName ?? "the previous product"}` : "unassigned";
        setLastPair({ ...p, productLabel: restored, update: data.latest_update });
      }
    },
    onError: (e) => fail(errorText(e)),
  });

  const findLabel = useMutation({
    mutationFn: async (tagId: string) => {
      const { data, error, response } = await api.POST("/tags/{tag_id}/locate", {
        params: { path: { tag_id: tagId } },
        body: { seconds: 30 },
      });
      if (!data) throw new ApiError(response, error);
      return data;
    },
    onSuccess: (data, tagId) => {
      if (tagId === tagIdRef.current) setLocate(data); // the user may have scanned another tag meanwhile
    },
    onError: (e) => fail(errorText(e)),
  });

  const { product, choices, tag } = state;

  return (
    <div className={`min-h-screen transition-colors ${flash ? "bg-green-50" : "bg-white"}`}>
      <header className="flex items-center justify-between gap-2 border-b border-gray-200 px-4 py-3">
        <button className="text-sm text-gray-600 underline" onClick={() => navigate("/")}>
          ← Dashboard
        </button>
        <select
          aria-label="Store"
          className="max-w-[60%] rounded border border-gray-300 px-2 py-1 text-sm"
          value={storeId ?? ""}
          onChange={(e) => {
            storeIdRef.current = e.target.value;
            setChosenStore(e.target.value);
            setLastStore(e.target.value);
            clearSlots();
            // Focused, the select would take scanner characters as type-ahead and switch store.
            e.currentTarget.blur();
          }}
        >
          {storeList.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </header>

      <main className="mx-auto max-w-md space-y-4 p-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Pair tags</h1>
          <button
            className="text-sm underline"
            onClick={() => {
              setCameraOn((on) => !on);
              setCameraFailed(false); // turning the camera back on retries it
            }}
          >
            {cameraOn ? "Use handheld scanner" : "Use camera"}
          </button>
        </div>

        {stores.isError && (
          <p className="rounded bg-red-100 px-3 py-2 text-sm text-red-700">{errorText(stores.error)}</p>
        )}
        {stores.isSuccess && storeList.length === 0 && (
          <p className="rounded bg-amber-50 p-3 text-sm text-amber-800">You don't have access to any store.</p>
        )}

        {cameraOn && storeId && <CameraScanner onScan={handleScan} onError={() => setCameraFailed(true)} />}
        {!cameraOn && (
          <p className="rounded bg-gray-50 p-3 text-sm text-gray-600">Ready for the handheld scanner.</p>
        )}

        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (manual.trim()) void handleScan(manual.trim());
            setManual("");
          }}
        >
          <input
            className="min-w-0 flex-1 rounded border border-gray-300 px-2 py-1 text-sm"
            placeholder="Or type a code"
            value={manual}
            onChange={(e) => setManual(e.target.value)}
          />
          <button className="rounded border border-gray-300 px-3 text-sm">Go</button>
        </form>

        {notice && <p className={`rounded px-3 py-2 text-sm ${TONES[notice.tone]}`}>{notice.text}</p>}

        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="min-w-0 rounded border border-gray-200 p-3">
            <h2 className="text-xs font-medium uppercase text-gray-500">Product</h2>
            {product ? (
              <>
                <p className="font-medium">{product.name}</p>
                {product.variationName !== "Regular" && <p className="text-sm text-gray-600">{product.variationName}</p>}
                <p className="text-lg font-semibold">{formatPrice(product.price, product.currency)}</p>
                {product.pricingType === "VARIABLE_PRICING" ? (
                  <p className="text-sm text-red-600">Variable-price items can't be shown on a label.</p>
                ) : product.price === null && (
                  <p className="text-sm text-red-600">This item has no price, so it can't be shown on a label.</p>
                )}
              </>
            ) : choices.length > 0 ? (
              <ul className="space-y-1">
                {choices.map((c) => (
                  <li key={c.id}>
                    <button
                      className="w-full rounded border border-gray-200 px-2 py-1 text-left text-sm hover:bg-gray-50"
                      onClick={() => dispatch({ type: "product-chosen", product: c })}
                    >
                      {c.name} · {c.variationName} · {formatPrice(c.price, c.currency)}
                    </button>
                  </li>
                ))}
              </ul>
            ) : notFound ? (
              <p className="break-words text-sm text-red-600">
                No product with code {notFound} at this store. Search by name below.
              </p>
            ) : (
              <p className="text-sm text-gray-400">Scan a product barcode</p>
            )}
          </div>

          <div className="min-w-0 rounded border border-gray-200 p-3">
            <h2 className="text-xs font-medium uppercase text-gray-500">Tag</h2>
            {tag ? (
              <>
                <p className="font-mono">{tag.tagId}</p>
                <p className="text-sm text-gray-600">
                  {tag.status === "new" && "New tag"}
                  {tag.status === "ready" && "Ready"}
                  {tag.status === "assigned" && `Currently showing ${tag.currentName ?? "a product"} — will be replaced`}
                  {tag.status === "other-store" && "This tag can't be used in this store"}
                </p>
                {!tag.seenByGateway && (
                  <p className="text-sm text-amber-700">This store's gateway hasn't seen this tag yet</p>
                )}
                {tag.status !== "new" && tag.status !== "other-store" && (
                  <button
                    className="mt-1 text-sm underline"
                    disabled={findLabel.isPending}
                    onClick={() => findLabel.mutate(tag.tagId)}
                  >
                    Find this label
                  </button>
                )}
                {locate && <UpdateStatus key={locate.update_id} initial={locate} />}
              </>
            ) : (
              <p className="text-sm text-gray-400">Scan a shelf tag</p>
            )}
          </div>
        </section>

        {product === null && choices.length === 0 && (
          <div className="space-y-2">
            <input
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
              placeholder="Search products by name"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {(results.data ?? []).map((v) => (
              <button
                key={v.id}
                className="block w-full rounded border border-gray-200 px-2 py-1 text-left text-sm hover:bg-gray-50"
                onClick={() => {
                  dispatch({ type: "product-chosen", product: toChoice(v) });
                  setNotFound(null);
                  setSearch("");
                }}
              >
                {v.name} · {v.variation_name} · {formatPrice(v.price ?? null, v.currency ?? null)}
              </button>
            ))}
          </div>
        )}

        {tag && (product || choices.length > 0) && (
          <div className="space-y-2 rounded border-2 border-blue-600 p-3">
            {product && (
              <p className="text-sm">
                <span className="font-mono">{tag.tagId.slice(0, 4)}…</span> → {product.name} ·{" "}
                {formatPrice(product.price, product.currency)}
              </p>
            )}
            <div className="flex gap-2">
              <button
                className="flex-1 rounded bg-blue-600 py-3 text-lg font-semibold text-white disabled:opacity-40"
                disabled={!readyToPair(state) || !storeId || pair.isPending}
                onClick={() => {
                  if (tag && product && storeId) pair.mutate({ tag, product, storeId });
                }}
              >
                {pair.isPending ? "Pairing…" : "Pair"}
              </button>
              <button className="rounded border border-gray-300 px-3 text-sm" onClick={clearSlots}>
                Clear
              </button>
            </div>
          </div>
        )}

        {lastPair && (
          <div className="space-y-2 rounded border border-gray-200 p-3">
            <p className="break-words text-sm">
              <span className="font-mono">{lastPair.tagId}</span> → {lastPair.productLabel}
            </p>
            <UpdateStatus key={lastPair.update.update_id} initial={lastPair.update} />
            {undoable && (
              <button className="text-sm underline" disabled={undo.isPending} onClick={() => undo.mutate(lastPair)}>
                Undo
              </button>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
