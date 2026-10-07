import { describe, expect, it } from "vitest";
import { WedgeBuffer } from "./wedge";

function typeKeys(buffer: WedgeBuffer, text: string, start: number, gap: number): number {
  let t = start;
  for (const ch of text) {
    buffer.push(ch, t);
    t += gap;
  }
  return t;
}

function type(buffer: WedgeBuffer, text: string, start: number, gap: number): string | null {
  return buffer.push("Enter", typeKeys(buffer, text, start, gap));
}

describe("WedgeBuffer", () => {
  it("emits a fast burst ending in Enter", () => {
    expect(type(new WedgeBuffer(), "840001262D51", 0, 10)).toBe("840001262D51");
  });

  it("ignores human-speed typing", () => {
    expect(type(new WedgeBuffer(), "hello world", 0, 200)).toBeNull();
  });

  it("ignores short bursts and non-character keys", () => {
    const b = new WedgeBuffer();
    b.push("Shift", 0);
    expect(type(b, "ab", 1, 5)).toBeNull();
  });

  it("starts fresh after a pause", () => {
    const b = new WedgeBuffer();
    typeKeys(b, "zz", 0, 5); // stray keys, no Enter
    expect(type(b, "036000291452", 1000, 8)).toBe("036000291452");
  });

  it("ignores an Enter that arrives after a pause", () => {
    const b = new WedgeBuffer();
    typeKeys(b, "840001262D51", 0, 10);
    expect(b.push("Enter", 1000)).toBeNull();
  });
});
