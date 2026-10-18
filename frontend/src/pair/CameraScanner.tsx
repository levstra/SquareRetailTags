import { useEffect, useRef, useState } from "react";
import { createDetector } from "./zxing";

const REPEAT_MS = 2000; // the same code held in view is reported once per 2 s

interface Props {
  onScan: (code: string) => void;
}

export function CameraScanner({ onScan }: Props) {
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
    let loggedFailure = false;
    const detector = createDetector();

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          // Without a size, phones give 640x480: too few pixels for a 12-char Code 128 tag barcode
          // unless it fills the frame. Ask for 1080p; the camera falls back to what it has.
          video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        });
        const [track] = stream.getVideoTracks();
        // Continuous autofocus where supported (Android Chrome); ignored elsewhere.
        await track
          ?.applyConstraints({ advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet] })
          .catch(() => {});
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
          } catch (e) {
            // An undecodable frame returns no hits; a throw means the decoder itself failed
            // (e.g. its .wasm didn't load). Say so once, or it looks like "no barcode found".
            if (!loggedFailure) console.error("Barcode decoder failed", e);
            loggedFailure = true;
          }
          timer = window.setTimeout(tick, 250);
        };
        void tick();
      } catch (e) {
        if (stopped) return;
        const message = e instanceof Error ? e.message : "Camera unavailable";
        setError(message);
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
      <p className="rounded-lg bg-warn-tint p-3 text-sm text-warn">
        Camera unavailable ({error}). Use a handheld scanner or type the code below.
      </p>
    );
  }
  return <video ref={videoRef} playsInline muted className="aspect-video w-full rounded-xl bg-black object-cover" />;
}
