let audio: AudioContext | null = null;

export function beep(ok = true): void {
  try {
    audio ??= new AudioContext();
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = ok ? 880 : 220;
    gain.gain.value = 0.1;
    osc.connect(gain).connect(audio.destination);
    osc.start();
    osc.stop(audio.currentTime + 0.12);
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
