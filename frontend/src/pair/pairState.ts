export interface ProductChoice {
  id: string;
  name: string;
  variationName: string;
  price: number | null;
  pricingType: string;
  currency: string | null;
}

export interface TagSlot {
  tagId: string;
  // new: unknown to us; ready: ours, unassigned; assigned: shows a product;
  // other-store: ours, but in a store this user can't use.
  status: "new" | "ready" | "assigned" | "other-store";
  currentName: string | null;
  previousVariationId: string | null;
  // The store the tag was in before this scan; undo moves it back there.
  previousStoreId: string | null;
  seenByGateway: boolean;
}

export interface PairState {
  product: ProductChoice | null;
  choices: ProductChoice[];
  tag: TagSlot | null;
}

export type PairAction =
  | { type: "product-found"; matches: ProductChoice[] }
  | { type: "product-chosen"; product: ProductChoice }
  | { type: "tag-found"; tag: TagSlot }
  | { type: "clear" };

export const initialPairState: PairState = { product: null, choices: [], tag: null };

export function pairReducer(state: PairState, action: PairAction): PairState {
  switch (action.type) {
    case "product-found":
      return action.matches.length === 1
        ? { ...state, product: action.matches[0], choices: [] }
        : { ...state, product: null, choices: action.matches };
    case "product-chosen":
      return { ...state, product: action.product, choices: [] };
    case "tag-found":
      return { ...state, tag: action.tag };
    case "clear":
      return initialPairState;
  }
}

export function readyToPair(state: PairState): boolean {
  return (
    state.product !== null &&
    state.tag !== null &&
    state.tag.status !== "other-store" &&
    state.product.pricingType !== "VARIABLE_PRICING"
  );
}
