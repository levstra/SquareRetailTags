import { describe, expect, it } from "vitest";
import { initialPairState, pairReducer, readyToPair, type ProductChoice, type TagSlot } from "./pairState";

const product: ProductChoice = {
  id: "VAR_A", name: "Booster Box", variationName: "Regular", price: 13494,
  pricingType: "FIXED_PRICING", currency: "USD",
};
const other: ProductChoice = { ...product, id: "VAR_B", variationName: "Big" };
const tag: TagSlot = {
  tagId: "840001262D51", status: "new", currentName: null, previousVariationId: null, previousStoreId: null,
  seenByGateway: true,
};

describe("pairReducer", () => {
  it("fills slots in either order", () => {
    const a = pairReducer(pairReducer(initialPairState, { type: "tag-found", tag }), { type: "product-found", matches: [product] });
    const b = pairReducer(pairReducer(initialPairState, { type: "product-found", matches: [product] }), { type: "tag-found", tag });
    expect(a).toEqual(b);
    expect(readyToPair(a)).toBe(true);
  });

  it("asks to choose when several products match", () => {
    const s = pairReducer(initialPairState, { type: "product-found", matches: [product, other] });
    expect(s.product).toBeNull();
    expect(s.choices).toHaveLength(2);
    const chosen = pairReducer(s, { type: "product-chosen", product: other });
    expect(chosen.product).toEqual(other);
    expect(chosen.choices).toEqual([]);
  });

  it("is not ready for other-store tags or variable prices", () => {
    const s = pairReducer(initialPairState, { type: "tag-found", tag: { ...tag, status: "other-store" } });
    expect(readyToPair(pairReducer(s, { type: "product-found", matches: [product] }))).toBe(false);
    const variable = pairReducer(
      pairReducer(initialPairState, { type: "tag-found", tag }),
      { type: "product-found", matches: [{ ...product, price: null, pricingType: "VARIABLE_PRICING" }] },
    );
    expect(readyToPair(variable)).toBe(false);
  });

  it("is not ready for a fixed-price item with no price", () => {
    const unpriced = pairReducer(
      pairReducer(initialPairState, { type: "tag-found", tag }),
      { type: "product-found", matches: [{ ...product, price: null }] },
    );
    expect(readyToPair(unpriced)).toBe(false);
  });

  it("clears", () => {
    const s = pairReducer(pairReducer(initialPairState, { type: "tag-found", tag }), { type: "clear" });
    expect(s).toEqual(initialPairState);
  });
});
