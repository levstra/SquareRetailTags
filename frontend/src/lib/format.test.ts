import { describe, expect, it } from "vitest";
import { formatPrice, tagStatus } from "./format";
import type { Tag } from "./queries";

const recent = new Date().toISOString();

function tag(overrides: Partial<Tag> = {}): Tag {
  return {
    id: "AAAAAAAAAAA1",
    content_hash: "h",
    signal: "ok",
    last_seen_at: recent,
    rf_power: -55,
    battery_volts: 3.0,
    low_battery: false,
    latest_update: null,
    ...overrides,
  } as Tag;
}

function update(status: "Pending" | "Dispatched" | "Succeeded" | "Failed", failure_code: string | null = null) {
  return { update_id: "u", action: "Update" as const, status, failure_code, retryable: null, status_at: recent };
}

describe("formatPrice", () => {
  it("splits cents with integer math", () => {
    expect(formatPrice(0)).toBe("$0.00");
    expect(formatPrice(5)).toBe("$0.05");
    expect(formatPrice(14999)).toBe("$149.99");
    expect(formatPrice(null)).toBe("Variable");
  });
});

describe("tagStatus", () => {
  it("is working when heard, charged and delivered", () => {
    expect(tagStatus(tag({ latest_update: update("Succeeded") }))).toMatchObject({ key: "ok", group: "ok" });
  });

  it("flags a failed delivery, naming the Pi's failure code", () => {
    const status = tagStatus(tag({ latest_update: update("Failed", "LabelNoResponse") }));
    expect(status).toMatchObject({ key: "update_failed", tone: "bad", group: "attention" });
    expect(status.detail).toContain("LabelNoResponse");
  });

  it("ranks a failed delivery after hearing problems but before battery and signal", () => {
    const failed = tagStatus(tag({ latest_update: update("Failed") })).rank;
    expect(tagStatus(tag({ signal: "never_heard" })).rank).toBeLessThan(failed);
    expect(tagStatus(tag({ signal: "out_of_range" })).rank).toBeLessThan(failed);
    expect(tagStatus(tag({ low_battery: true })).rank).toBeGreaterThan(failed);
    expect(tagStatus(tag({ signal: "weak" })).rank).toBeGreaterThan(failed);
  });

  it("puts weak signal in its own group, not attention, while updates land", () => {
    const status = tagStatus(tag({ signal: "weak", rf_power: -74, latest_update: update("Succeeded") }));
    expect(status).toMatchObject({ key: "weak", group: "watch" });
    expect(status.detail).toContain("-74 dBm");
  });

  it("still flags a weak-signal tag whose update failed or whose battery is low", () => {
    expect(tagStatus(tag({ signal: "weak", latest_update: update("Failed") }))).toMatchObject({
      key: "update_failed",
      group: "attention",
    });
    expect(tagStatus(tag({ signal: "weak", low_battery: true }))).toMatchObject({
      key: "low_battery",
      group: "attention",
    });
  });

  it("mentions an update still on its way without raising the alarm", () => {
    for (const s of ["Pending", "Dispatched"] as const) {
      const status = tagStatus(tag({ latest_update: update(s) }));
      expect(status).toMatchObject({ key: "ok", group: "ok" });
      expect(status.detail).toContain("update sending");
    }
  });
});
