import type { Tag, Variation } from "./queries";

/** Money is an integer in the smallest currency unit — 14999 is $149.99.
 *
 * Split with integer arithmetic and reassemble as a string: `cents / 100`
 * alone would put the value through a float on its way to the screen. Null
 * means VARIABLE_PRICING, which has no price to show. */
export function formatPrice(cents: number | null | undefined): string {
  if (cents == null) return "Variable";
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const whole = (abs - (abs % 100)) / 100;
  const fraction = abs % 100;
  const sign = negative ? "-" : "";
  return `${sign}$${whole.toLocaleString("en-US")}.${String(fraction).padStart(2, "0")}`;
}

/** Same hiding rule the label hardware itself uses: a "Regular" variation
 * name, or one that just repeats the item name, adds no information.
 *
 * Applied to both shapes that carry these two fields — a catalog Variation and
 * a Tag's denormalized copy of it — so a row and its product card agree. */
function keptVariationName(
  name: string | null | undefined,
  variationName: string | null | undefined,
): string | null {
  if (!variationName) return null;
  if (variationName === "Regular" || variationName === name) return null;
  return variationName;
}

export function variationLabel(v: Variation): string | null {
  return keptVariationName(v.name, v.variation_name);
}

export function tagVariationLabel(t: Tag): string | null {
  return keptVariationName(t.name, t.variation_name);
}

export function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export type Tone = "ok" | "warn" | "bad" | "idle";

export type TagStatus = {
  key: "never_heard" | "out_of_range" | "update_failed" | "low_battery" | "weak" | "ok";
  /** Plain words. Staff reading this are looking at a shelf, not a protocol. */
  label: string;
  tone: Tone;
  /** The specifics behind the label — volts, dBm, how long ago. */
  detail: string;
  /** Which section of the Labels view the tag belongs in. `watch` is working
   * (its updates are landing) but worth keeping an eye on: weak signal. */
  group: "attention" | "watch" | "ok";
  /** Worst first, for sorting. */
  rank: number;
};

/** One status per tag, by precedence, because a row shows one headline.
 *
 * A dead battery outranks a weak signal: it needs someone to walk over with a
 * replacement, where weak signal often resolves itself. Anything the headline
 * displaces still shows up in `detail` and in the row's expanded fields, so
 * picking a headline never hides a second problem. */
export function tagStatus(tag: Tag): TagStatus {
  const volts = tag.battery_volts;
  const battery = volts != null ? `${volts.toFixed(1)} V` : null;
  const heard = tag.last_seen_at ? `last heard ${relativeTime(tag.last_seen_at)}` : null;
  const power = tag.rf_power != null ? `${tag.rf_power} dBm` : null;

  const and = (...parts: (string | null)[]) => parts.filter(Boolean).join(" · ");

  // The latest command sent to the store's Pi for this tag.
  const update = tag.latest_update;
  const sending =
    update && (update.status === "Pending" || update.status === "Dispatched")
      ? "update sending"
      : null;

  if (tag.signal === "never_heard") {
    return {
      key: "never_heard",
      label: "Never seen",
      tone: "idle",
      detail: "no access point has ever heard this tag — the ID may be wrong",
      group: "attention",
      rank: 0,
    };
  }

  if (tag.signal === "out_of_range") {
    return {
      key: "out_of_range",
      label: "Not responding",
      tone: "bad",
      detail: and(heard, tag.low_battery ? `low battery, ${battery}` : battery),
      group: "attention",
      rank: 1,
    };
  }

  // The shelf may be showing old content. Ranked after the hearing problems,
  // which usually explain the failure and say what to do about it.
  if (update?.status === "Failed") {
    return {
      key: "update_failed",
      label: "Update failed",
      tone: "bad",
      detail: and(
        update.failure_code ? `the gateway reported ${update.failure_code}` : "the gateway couldn't apply it",
        update.retryable === false ? "needs a person" : null,
      ),
      group: "attention",
      rank: 2,
    };
  }

  if (tag.low_battery) {
    return {
      key: "low_battery",
      label: "Low battery",
      tone: "warn",
      detail: and(battery ? `${battery} · swap it soon` : "swap it soon", power, sending),
      group: "attention",
      rank: 3,
    };
  }

  // Not a problem on its own: a failed update would have returned above, so a
  // weak-signal tag is still taking its updates. Its own section, not attention.
  if (tag.signal === "weak") {
    return {
      key: "weak",
      label: "Weak signal",
      tone: "warn",
      detail: and(power, battery, heard, sending),
      group: "watch",
      rank: 4,
    };
  }

  return {
    key: "ok",
    label: "Working",
    tone: "ok",
    detail: and(power, battery, sending),
    group: "ok",
    rank: 5,
  };
}
