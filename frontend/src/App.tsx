import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, ApiError, clearRedirectMarker } from "./api/client";
import { signOut } from "./auth/userManager";
import { AssignModal, type AssignTarget } from "./features/AssignModal";
import { ItemDetailModal } from "./features/ItemDetailModal";
import { LabelList } from "./features/LabelList";
import { ProductList } from "./features/ProductList";
import { StoresModal } from "./features/StoresModal";
import type { Variation } from "./lib/queries";
import { navigate } from "./router";
import { SquareSettings } from "./square/SquareSettings";
import { squareConnectionQuery } from "./square/queries";
import { StaffPanel } from "./staff/StaffPanel";
import { Button, ThemeToggle } from "./ui";

type View = "labels" | "products" | "staff" | "square";

/** The wordmark. The rounded rectangle is a shelf label with a price line —
 * swap it for the company mark when there is one. */
export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="2" y="5" width="20" height="14" rx="3" className="fill-accent" />
        <rect x="5.5" y="8.5" width="8" height="2" rx="1" className="fill-on-accent" />
        <rect
          x="5.5"
          y="12.5"
          width="13"
          height="3"
          rx="1.5"
          className="fill-on-accent"
          opacity="0.55"
        />
      </svg>
      <span className="text-[15px] font-bold tracking-tight text-ink">
        ESL Pricing
      </span>
    </span>
  );
}

function NavLink({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      className={clsx(
        "min-h-10 rounded-lg px-3.5 text-sm transition-colors",
        active
          ? "bg-accent font-semibold text-on-accent"
          : "font-medium text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

// Square's OAuth callback lands on /settings/square?result=...
const returnedFromSquare = window.location.pathname === "/settings/square";
const squareResult = returnedFromSquare
  ? new URLSearchParams(window.location.search).get("result")
  : null;

export default function App() {
  const [view, setView] = useState<View>(returnedFromSquare ? "square" : "labels");
  const [assigning, setAssigning] = useState<AssignTarget | "new" | null>(null);
  const [selected, setSelected] = useState<Variation | null>(null);
  const [storesOpen, setStoresOpen] = useState(false);

  useEffect(() => {
    if (window.location.pathname !== "/") window.history.replaceState({}, document.title, "/");
  }, []);

  const { data: me, error: meError } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/me");
      if (!response.ok || !data) throw new ApiError(response, error);
      clearRedirectMarker();
      return data;
    },
    retry: false,
  });

  const { data: square } = useQuery({ ...squareConnectionQuery, enabled: !!me });
  const squareNeedsAttention = square != null && square.status !== "active";
  const isOwner = me?.role === "owner";

  if (meError) {
    const detail = meError instanceof ApiError ? meError.detail : null;
    const meUnauthorized = meError instanceof ApiError && meError.status === 401;
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-5 px-6">
        <Logo />
        <p className="max-w-md text-center text-sm text-bad">
          {meUnauthorized
            ? "We couldn't verify your sign-in. Try signing out and in again; if it keeps happening, sign-in may be misconfigured."
            : (detail ?? "Your account doesn't have access to this dashboard.")}
        </p>
        <Button variant="secondary" onClick={() => void signOut()}>
          Sign out
        </Button>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-3.5 border-b border-line-soft px-7 py-4">
        <div className="flex flex-col">
          <Logo />
          {me?.business.name && (
            <span className="mt-0.5 pl-[34px] text-xs text-faint">{me.business.name}</span>
          )}
        </div>

        <nav className="flex flex-wrap gap-1" aria-label="Main">
          <NavLink active={view === "labels"} onClick={() => setView("labels")}>
            Labels
          </NavLink>
          <NavLink active={view === "products"} onClick={() => setView("products")}>
            Products
          </NavLink>
          {isOwner && (
            <NavLink active={view === "staff"} onClick={() => setView("staff")}>
              Staff
            </NavLink>
          )}
          {isOwner && (
            <NavLink active={view === "square"} onClick={() => setView("square")}>
              Square
            </NavLink>
          )}
        </nav>

        <div className="flex flex-1 flex-wrap items-center justify-end gap-2.5">
          <ThemeToggle />
          <Button onClick={() => navigate("/pair")}>Pair tags</Button>
          <Button onClick={() => setStoresOpen(true)}>Stores</Button>
          {me?.email && (
            <span className="text-[13px] text-faint">
              {me.email} · {me.role}
            </span>
          )}
          <Button variant="quiet" onClick={() => void signOut()}>
            Sign out
          </Button>
        </div>
      </header>

      {squareNeedsAttention && (
        <div className="border-b border-line-soft bg-warn-tint px-7 py-2.5 text-sm text-warn">
          {isOwner ? (
            <>
              Prices aren't syncing from Square.{" "}
              <button
                type="button"
                className="font-semibold underline"
                onClick={() => setView("square")}
              >
                {square?.status === "not_connected" ? "Connect Square" : "Reconnect Square"}
              </button>
            </>
          ) : (
            "Prices aren't syncing from Square. Ask the business owner to reconnect Square."
          )}
        </div>
      )}

      <main className="mx-auto max-w-5xl px-6 py-9 pb-14">
        {view === "square" && !me ? (
          <p className="py-12 text-center text-sm text-muted">Loading…</p>
        ) : view === "staff" && isOwner ? (
          <StaffPanel />
        ) : view === "square" && isOwner ? (
          <SquareSettings result={squareResult} />
        ) : view === "products" ? (
          <ProductList onSelect={setSelected} />
        ) : (
          <LabelList
            onAddTag={() => setAssigning("new")}
            onAssign={(tag) =>
              setAssigning({
                tagId: tag.id,
                storeId: tag.store_id ?? undefined,
                variationId: tag.variation_id,
              })
            }
          />
        )}
      </main>

      {selected && (
        <ItemDetailModal variation={selected} onClose={() => setSelected(null)} />
      )}

      {assigning !== null && (
        <AssignModal
          initial={assigning === "new" ? undefined : assigning}
          onClose={() => setAssigning(null)}
        />
      )}

      {storesOpen && (
        <StoresModal isOwner={isOwner} onClose={() => setStoresOpen(false)} />
      )}
    </div>
  );
}
