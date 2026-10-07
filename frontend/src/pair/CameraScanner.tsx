import { useEffect, useRef, useState } from "react";
import { createDetector } from "./zxing";

const REPEAT_MS = 2000; // the same code held in view is reported once per 2 s

export function CameraScanner({ onScan }: { onScan: (code: string) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onScanRef = useRef(onScan);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    onScanRef.current = onScan;
  }, [onScan]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer: number | undefined;
    let stopped = false;
    let last = { code: "", at: 0 };
    const detector = createDetector();

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
          audio: false,
        });
        const video = videoRef.current;
        if (stopped || !video) {
          // Unmounted (or toggled off) while the permission prompt was open.
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        video.srcObject = stream;
        await video.play();
        const tick = async () => {
          if (stopped) return;
          try {
            if (video.readyState >= 2) {
              const [hit] = await detector.detect(video);
              const now = Date.now();
              if (hit?.rawValue && (hit.rawValue !== last.code || now - last.at > REPEAT_MS)) {
                last = { code: hit.rawValue, at: now };
                onScanRef.current(hit.rawValue);
              }
            }
          } catch {
            // Frame not decodable yet; try the next one.
          }
          timer = window.setTimeout(tick, 250);
        };
        void tick();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Camera unavailable");
      }
    };
    void start();

    return () => {
      stopped = true;
      window.clearTimeout(timer);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  if (error) {
    return (
      <p className="rounded bg-amber-50 p-3 text-sm text-amber-800">
        Camera unavailable ({error}). Use a handheld scanner or type the code below.
      </p>
    );
  }
  return <video ref={videoRef} playsInline muted className="aspect-video w-full rounded bg-black object-cover" />;
}
