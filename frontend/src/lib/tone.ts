import type { Tone } from "./format";

/* Tone → token class. Kept out of ui.tsx so that file exports only
 * components (fast refresh stops working on a module that mixes the two).
 *
 * These are whole literal class strings on purpose: Tailwind scans source
 * text, so a composed string like `text-${tone}` would generate nothing. */

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  idle: "text-faint",
};

const TONE_BAR: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn-dot",
  bad: "bg-bad",
  idle: "bg-faint",
};

/** A left edge in the status color, for a row that needs something. Carries no
 * meaning on its own — the row always states the status in words too. */
const TONE_EDGE: Record<Tone, string> = {
  ok: "border-l-4 border-l-ok",
  warn: "border-l-4 border-l-warn-dot",
  bad: "border-l-4 border-l-bad",
  idle: "border-l-4 border-l-faint",
};

const TONE_TINT: Record<Tone, string> = {
  ok: "bg-ok-tint text-ok",
  warn: "bg-warn-tint text-warn",
  bad: "bg-bad-tint text-bad",
  idle: "bg-surface-2 text-muted",
};

export function toneText(tone: Tone) {
  return TONE_TEXT[tone];
}

export function toneBar(tone: Tone) {
  return TONE_BAR[tone];
}

export function toneEdge(tone: Tone) {
  return TONE_EDGE[tone];
}

export function toneTint(tone: Tone) {
  return TONE_TINT[tone];
}
