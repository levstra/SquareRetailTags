import { describe, expect, it } from "vitest";
import { statusMessage } from "./statusMessages";

const base = { action: "Update", failure_code: null, retryable: null } as const;

describe("statusMessage", () => {
  it("maps the happy path", () => {
    expect(statusMessage({ ...base, status: "Pending" })).toMatchObject({ tone: "pending", final: false });
    expect(statusMessage({ ...base, status: "Dispatched" }).text).toMatch(/about 16 s/);
    expect(statusMessage({ ...base, status: "Succeeded" })).toEqual({ tone: "success", text: "On the shelf", final: true });
  });

  it("uses the contract's locate wording", () => {
    expect(statusMessage({ ...base, action: "Locate", status: "Succeeded" }).text).toBe("Label is blinking");
  });

  it("maps every failure code from the Pi contract", () => {
    const fail = (code: string, retryable: boolean) =>
      statusMessage({ ...base, status: "Failed", failure_code: code, retryable });
    expect(fail("LabelNoResponse", true).text).toMatch(/not in range of any access point/);
    expect(fail("ApUnavailable", true).text).toMatch(/No access point online/);
    expect(fail("ApTimeout", true).text).toMatch(/did not answer/);
    expect(fail("ApQueueFull", true).text).toMatch(/busy/);
    expect(fail("LabelRejectedKey", false).text).toMatch(/needs attention/);
    expect(fail("LabelHardwareError", false).text).toMatch(/needs attention/);
    expect(fail("RenderError", false).text).toMatch(/couldn't draw/);
    expect(fail("InvalidCommand", false).text).toMatch(/rejected/);
    expect(fail("SomethingNew", false).text).toBe("Update failed");
  });

  it("says retryable failures are retried and are warnings", () => {
    const m = statusMessage({ ...base, status: "Failed", failure_code: "ApTimeout", retryable: true });
    expect(m.tone).toBe("warning");
    expect(m.text).toMatch(/retrying automatically/);
    expect(m.final).toBe(true);
  });

  it("doesn't ask for a person while a hardware error is being retried", () => {
    const m = statusMessage({ ...base, status: "Failed", failure_code: "LabelHardwareError", retryable: true });
    expect(m.tone).toBe("warning");
    expect(m.text).toMatch(/retrying automatically/);
    expect(m.text).not.toMatch(/needs attention/);
  });

  it("does not promise a retry for a failed locate", () => {
    const m = statusMessage({ ...base, action: "Locate", status: "Failed", failure_code: "ApTimeout", retryable: true });
    expect(m.tone).toBe("error");
    expect(m.text).not.toMatch(/retrying/);
    expect(m.final).toBe(true);
  });

  it("treats superseded as final and not an error", () => {
    expect(statusMessage({ ...base, status: "Superseded" })).toMatchObject({ tone: "success", final: true });
  });
});
