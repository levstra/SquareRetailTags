import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema.d.ts";
import { Button, Field, StatusLabel, TextInput } from "../ui";

type StaffUser = components["schemas"]["StaffUser"];
type Store = components["schemas"]["Store"];
type StaffUpdate = components["schemas"]["StaffUpdate"];

type Access = Pick<StaffUser, "all_stores" | "store_ids">;

const UPDATE_STAFF = ["updateStaff"];
// The API's invite default is no store access; the form starts on "All stores".
const NEW_STAFF_ACCESS: Access = { all_stores: true, store_ids: [] };

function message(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") {
    return error.detail;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function StoreChecklist({
  stores,
  access,
  onChange,
  disabled = false,
}: {
  stores: Store[];
  access: Access;
  onChange: (access: Access) => void;
  disabled?: boolean;
}) {
  // "All stores" is the all_stores flag. Specific stores are an explicit list,
  // and an empty list means no store, never every store.
  const specific = access.all_stores ? [] : access.store_ids;
  const none = !access.all_stores && access.store_ids.length === 0;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <label className="flex min-h-10 items-center gap-1.5 text-sm text-ink">
        <input
          type="checkbox"
          checked={access.all_stores}
          disabled={disabled}
          onChange={(e) => onChange({ all_stores: e.target.checked, store_ids: [] })}
        />
        All stores
      </label>
      {stores.map((s) => (
        <label key={s.id} className="flex min-h-10 items-center gap-1.5 text-sm text-ink">
          <input
            type="checkbox"
            checked={specific.includes(s.id)}
            disabled={disabled}
            onChange={(e) =>
              onChange({
                all_stores: false,
                store_ids: e.target.checked ? [...specific, s.id] : specific.filter((id) => id !== s.id),
              })
            }
          />
          {s.name}
        </label>
      ))}
      {none && <span className="text-sm font-semibold text-warn">No store access</span>}
    </div>
  );
}

export function StaffPanel() {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [inviteAccess, setInviteAccess] = useState<Access>(NEW_STAFF_ACCESS);
  const [error, setError] = useState("");

  const staff = useQuery({
    queryKey: ["staff"],
    queryFn: async () => {
      const { data, error } = await api.GET("/staff");
      if (error) throw error;
      return data.users;
    },
  });
  const stores = useQuery({
    queryKey: ["stores"],
    queryFn: async () => {
      const { data, error } = await api.GET("/stores");
      if (error) throw error;
      return data.stores;
    },
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["staff"] });

  const invite = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/staff", { body: { email, ...inviteAccess } });
      if (error) throw error;
    },
    onSuccess: () => {
      setEmail("");
      setInviteAccess(NEW_STAFF_ACCESS);
      setError("");
      void refresh();
    },
    onError: (e) => setError(message(e)),
  });

  const update = useMutation({
    mutationKey: UPDATE_STAFF,
    mutationFn: async ({ id, body }: { id: string; body: StaffUpdate }) => {
      const { error } = await api.PATCH("/staff/{user_id}", { params: { path: { user_id: id } }, body });
      if (error) throw error;
    },
    onSuccess: () => setError(""),
    onError: (e) => setError(message(e)),
    // Returning the refetch keeps the mutation pending until the row shows
    // fresh access, so the next click can't build on stale ones.
    onSettled: () => refresh(),
  });

  // Every row with an update in flight, not just the latest one.
  const pendingIds = new Set(
    useMutationState({
      filters: { mutationKey: UPDATE_STAFF, status: "pending" },
      select: (m) => (m.state.variables as { id: string } | undefined)?.id,
    }),
  );

  const allStores = stores.data ?? [];

  return (
    <section>
      <h1 className="text-3xl font-bold tracking-tight text-ink">Staff</h1>
      <p className="mt-1.5 mb-7 text-[15px] text-muted">
        Who can sign in, and which stores they can work in.
      </p>

      <form
        className="mb-7 flex flex-col gap-3.5 rounded-xl border border-line bg-surface p-5"
        onSubmit={(e) => {
          e.preventDefault();
          invite.mutate();
        }}
      >
        <h2 className="text-xs font-bold tracking-wider text-muted uppercase">Invite staff</h2>
        <Field label="Email" hint="They get an email with a temporary password">
          <TextInput
            type="email"
            required
            placeholder="name@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <StoreChecklist stores={allStores} access={inviteAccess} onChange={setInviteAccess} />
        <Button type="submit" variant="primary" className="self-start" disabled={invite.isPending}>
          {invite.isPending ? "Sending…" : "Send invite"}
        </Button>
      </form>

      {error && <p className="mb-4 rounded-lg bg-bad-tint px-3 py-2 text-sm text-bad">{error}</p>}
      {(staff.error || stores.error) && (
        <p className="mb-4 rounded-lg bg-bad-tint px-3 py-2 text-sm text-bad">
          {message(staff.error ?? stores.error)}
        </p>
      )}

      <ul className="flex list-none flex-col gap-2 p-0">
        {(staff.data ?? []).map((u: StaffUser) => (
          <li
            key={u.id}
            className={`rounded-xl border border-line-soft bg-surface p-4 ${u.disabled ? "opacity-60" : ""}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2">
              <div className="min-w-0">
                <p className="text-[15px] font-medium text-ink">{u.email}</p>
                <p className="mt-0.5 text-xs text-faint">
                  {u.role}
                  {u.disabled ? " · disabled" : ""}
                </p>
              </div>
              {u.role === "staff" && (
                <Button
                  variant="quiet"
                  className={u.disabled ? "" : "text-bad"}
                  disabled={pendingIds.has(u.id)}
                  onClick={() => update.mutate({ id: u.id, body: { disabled: !u.disabled } })}
                >
                  {u.disabled ? "Enable" : "Disable"}
                </Button>
              )}
            </div>
            <div className="mt-2.5">
              {u.role === "owner" ? (
                <StatusLabel tone="idle">All stores</StatusLabel>
              ) : (
                <StoreChecklist
                  stores={allStores}
                  access={{ all_stores: u.all_stores, store_ids: u.store_ids }}
                  disabled={pendingIds.has(u.id)}
                  onChange={(access) => update.mutate({ id: u.id, body: access })}
                />
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
