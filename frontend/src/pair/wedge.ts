// Handheld barcode scanners "type" the code very fast, then press Enter.
export const WEDGE_MAX_GAP_MS = 60;
export const WEDGE_MIN_LENGTH = 4;

export class WedgeBuffer {
  private chars = "";
  private last = Number.NEGATIVE_INFINITY;

  /** A key arriving now continues a scanner burst (a character came within the gap). */
  inBurst(nowMs: number): boolean {
    return this.chars.length > 0 && nowMs - this.last <= WEDGE_MAX_GAP_MS;
  }

  push(key: string, nowMs: number): string | null {
    if (key === "Enter") {
      const code = this.chars;
      const stale = nowMs - this.last > WEDGE_MAX_GAP_MS;
      this.chars = "";
      return !stale && code.length >= WEDGE_MIN_LENGTH ? code : null;
    }
    if (key.length !== 1) return null; // Shift, arrows, etc.
    if (nowMs - this.last > WEDGE_MAX_GAP_MS) this.chars = "";
    this.chars += key;
    this.last = nowMs;
    return null;
  }
}
