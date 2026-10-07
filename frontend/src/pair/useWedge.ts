import { useEffect, useRef } from "react";
import { WedgeBuffer } from "./wedge";

export function useWedge(enabled: boolean, onScan: (code: string) => void): void {
  // Keep the latest callback without rebuilding the listener, so a re-render
  // mid-scan doesn't reset the buffer and emit a truncated code.
  const onScanRef = useRef(onScan);
  useEffect(() => {
    onScanRef.current = onScan;
  }, [onScan]);

  useEffect(() => {
    if (!enabled) return;
    const buffer = new WedgeBuffer();
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return; // typing in a field is not a scan
      }
      const code = buffer.push(e.key, e.timeStamp);
      if (code) {
        e.preventDefault();
        onScanRef.current(code);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
