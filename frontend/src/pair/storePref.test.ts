import { afterEach, describe, expect, it, vi } from "vitest";
import { getLastStore, setLastStore } from "./storePref";

afterEach(() => vi.unstubAllGlobals());

describe("storePref", () => {
  it("round-trips through localStorage", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    });
    setLastStore("store-1");
    expect(getLastStore()).toBe("store-1");
  });

  it("survives storage that throws (private mode, blocked site data)", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
    });
    expect(() => setLastStore("x")).not.toThrow();
    expect(getLastStore()).toBeNull();
  });
});
