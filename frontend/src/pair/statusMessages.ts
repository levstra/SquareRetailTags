import type { components } from "../api/schema.d.ts";

type Summary = components["schemas"]["LabelUpdateSummary"];
export type Tone = "pending" | "success" | "warning" | "error";
export interface StatusMessage {
  tone: Tone;
  text: string;
  final: boolean;
}

// Wording follows the Pi contract's suggested UI mapping (§9.1) and failure codes (§3.2).
const FAILURES: Record<string, string> = {
  LabelNoResponse: "Label not in range of any access point",
  ApUnavailable: "No access point online in this store",
  ApTimeout: "Access point did not answer",
  ApQueueFull: "Access point is busy",
  LabelRejectedKey: "Label rejected the update (key mismatch)",
  LabelHardwareError: "Label hardware error",
  RenderError: "The gateway couldn't draw this label",
  InvalidCommand: "The gateway rejected this update",
};

// Faults on the label itself: once the service stops retrying, someone has to go look.
const NEEDS_A_PERSON = new Set(["LabelRejectedKey", "LabelHardwareError"]);

export function statusMessage(
  u: Pick<Summary, "action" | "status" | "failure_code" | "retryable">,
): StatusMessage {
  switch (u.status) {
    case "Pending":
      return { tone: "pending", text: "Sending…", final: false };
    case "Dispatched":
      return { tone: "pending", text: "Gateway has it — updating the label (about 16 s)", final: false };
    case "Succeeded":
      return {
        tone: "success",
        text: u.action === "Locate" ? "Label is blinking" : "On the shelf",
        final: true,
      };
    case "Superseded":
      return { tone: "success", text: "Replaced by a newer change", final: true };
    case "Failed": {
      const reason = (u.failure_code && FAILURES[u.failure_code]) || "Update failed";
      // Locate is never retried by the service, so don't promise it.
      if (u.retryable && u.action !== "Locate") {
        return { tone: "warning", text: `${reason} — retrying automatically`, final: true };
      }
      const ask = u.failure_code && NEEDS_A_PERSON.has(u.failure_code) ? " — needs attention" : "";
      return { tone: "error", text: `${reason}${ask}`, final: true };
    }
    default:
      return { tone: "pending", text: "Sending…", final: false };
  }
}
