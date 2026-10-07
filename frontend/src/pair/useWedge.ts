import { useEffect } from "react";
import { WedgeBuffer } from "./wedge";

export function useWedge(enabled: boolean, onScan: (code: string) => void): void {
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
        onScan(code);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, onScan]);
}
