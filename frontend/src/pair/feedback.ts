let audio: AudioContext | null = null;

function audioContext(): AudioContext {
  audio ??= new AudioContext();
  if (audio.state === "suspended") void audio.resume().catch(() => {});
  return audio;
}

// iOS keeps an AudioContext suspended unless it is created or resumed inside a user
// gesture. Scans arrive from the camera or a timer, never a gesture, so unlock the
// context on the first tap or key. pointerup/touchend/keydown are the events that
// grant user activation for touch (pointerdown counts only for a mouse).
const UNLOCK_EVENTS = ["pointerup", "touchend", "keydown"] as const;

export function unlockAudioOnFirstGesture(): () => void {
  const unlock = () => {
    try {
      audioContext();
    } catch {
      // No audio available.
    }
    remove();
  };
  const remove = () => UNLOCK_EVENTS.forEach((e) => window.removeEventListener(e, unlock));
  UNLOCK_EVENTS.forEach((e) => window.addEventListener(e, unlock));
  return remove;
}

export function beep(ok = true): void {
  try {
    const ctx = audioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = ok ? 880 : 220;
    gain.gain.value = 0.1;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.12);
  } catch {
    // No audio available; the visual flash still shows.
  }
}

export function vibrate(ms = 80): void {
  try {
    navigator.vibrate?.(ms); // not supported on iOS; harmless there
  } catch {
    // ignore
  }
}
