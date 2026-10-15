import clsx from "clsx";
import {
  CircleCheck,
  CircleDashed,
  CircleX,
  Monitor,
  Moon,
  Search,
  Sun,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import type { Tone } from "./lib/format";
import { toneText, toneTint } from "./lib/tone";
import { useTheme, type ThemeChoice } from "./theme";

/* ── Buttons ───────────────────────────────────────────────────────────────── */

type ButtonVariant = "primary" | "secondary" | "quiet";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-hover font-semibold",
  secondary:
    "border border-line bg-surface text-muted hover:text-ink hover:border-accent-line",
  quiet: "text-muted hover:text-ink",
};

export function Button({
  variant = "secondary",
  className,
  ...rest
}: { variant?: ButtonVariant } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={clsx(
        // 40px min height everywhere: these get tapped on a phone on the floor.
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3.5",
        "text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        BUTTON_VARIANT[variant],
        className,
      )}
      {...rest}
    />
  );
}

/* ── Status ────────────────────────────────────────────────────────────────── */

const TONE_ICON = {
  ok: CircleCheck,
  warn: TriangleAlert,
  bad: CircleX,
  idle: CircleDashed,
} as const;

/** Status reads three ways at once — icon shape, color, and words — so it
 * survives color blindness, a grayscale print, and a glance. */
export function StatusLabel({ tone, children }: { tone: Tone; children: ReactNode }) {
  const Icon = TONE_ICON[tone];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-2 text-sm font-semibold",
        toneText(tone),
      )}
    >
      <Icon size={15} strokeWidth={2.2} className="shrink-0" aria-hidden="true" />
      {children}
    </span>
  );
}

export function Count({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      className={clsx(
        "rounded-full px-2.5 py-0.5 text-xs font-semibold tabular-nums",
        toneTint(tone),
      )}
    >
      {children}
    </span>
  );
}

/* ── Form controls ─────────────────────────────────────────────────────────── */

const CONTROL =
  "w-full min-h-11 rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-faint";

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

export function TextInput({
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={clsx(CONTROL, className)} {...rest} />;
}

export function Select({
  className,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={clsx(CONTROL, "pr-8", className)} {...rest} />;
}

export function SearchInput({
  label,
  className,
  ...rest
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className={clsx("relative", className)}>
      <Search
        size={18}
        className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-faint"
        aria-hidden="true"
      />
      <input
        type="search"
        aria-label={label}
        className="min-h-13 w-full rounded-xl border border-line bg-surface pr-4 pl-11 text-[15px] text-ink placeholder:text-faint"
        {...rest}
      />
    </div>
  );
}

/* ── Modal ─────────────────────────────────────────────────────────────────── */

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // Move focus into the dialog so Tab stays somewhere sensible and screen
    // readers announce the heading rather than the page behind it.
    panel.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={clsx(
          "max-h-[90vh] w-full overflow-y-auto rounded-2xl border border-line bg-surface p-6 shadow-2xl outline-none",
          wide ? "max-w-lg" : "max-w-md",
        )}
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <h2 className="text-lg font-bold tracking-tight text-ink">{title}</h2>
          <Button
            variant="quiet"
            aria-label="Close"
            onClick={onClose}
            className="-mt-1 -mr-1.5 min-h-10 w-10 px-0"
          >
            <X size={18} aria-hidden="true" />
          </Button>
        </div>

        {children}

        {footer && <div className="mt-6 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

/* ── States ────────────────────────────────────────────────────────────────── */

export function Loading({ what }: { what: string }) {
  return <p className="py-12 text-center text-sm text-muted">Loading {what}…</p>;
}

export function ErrorState({ what, error }: { what: string; error: unknown }) {
  return (
    <div className="rounded-xl border border-line bg-bad-tint p-4">
      <p className="text-sm font-semibold text-bad">Couldn’t load {what}.</p>
      <p className="mt-1 text-xs break-all text-muted">{String(error)}</p>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed border-line py-10 text-center text-sm text-faint">
      {children}
    </p>
  );
}

/* ── Theme toggle ──────────────────────────────────────────────────────────── */

const CHOICES: { value: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "Match system", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

export function ThemeToggle() {
  const { choice, choose } = useTheme();

  return (
    <div
      className="inline-flex rounded-lg border border-line bg-surface p-0.5"
      role="group"
      aria-label="Color theme"
    >
      {CHOICES.map(({ value, label, Icon }) => {
        const active = choice === value;
        return (
          <button
            key={value}
            type="button"
            aria-label={label}
            aria-pressed={active}
            onClick={() => choose(value)}
            className={clsx(
              "inline-flex min-h-9 w-10 items-center justify-center rounded-md transition-colors",
              active
                ? "bg-accent-tint text-accent-ink"
                : "text-faint hover:text-ink",
            )}
          >
            <Icon size={16} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
