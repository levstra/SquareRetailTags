import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema.d.ts";

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
      <label className="flex items-center gap-1 text-sm">
        <input
          type="checkbox"
          checked={access.all_stores}
          disabled={disabled}
          onChange={(e) => onChange({ all_stores: e.target.checked, store_ids: [] })}
        />
        All stores
      </label>
      {stores.map((s) => (
        <label key={s.id} className="flex items-center gap-1 text-sm">
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
      {none && <span className="text-sm text-amber-700">No store access</span>}
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
    <section className="space-y-6">
      <form
        className="space-y-2 rounded border border-gray-200 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          invite.mutate();
        }}
      >
        <h2 className="font-medium">Invite staff</h2>
        <input
          type="email"
          required
          placeholder="name@example.com"
          className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <StoreChecklist stores={allStores} access={inviteAccess} onChange={setInviteAccess} />
        <button
          type="submit"
          disabled={invite.isPending}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {invite.isPending ? "Sending…" : "Send invite"}
        </button>
      </form>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {(staff.error || stores.error) && (
        <p className="text-sm text-red-600">{message(staff.error ?? stores.error)}</p>
      )}

      <table className="w-full text-sm">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="py-2">Email</th>
            <th>Role</th>
            <th>Stores</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(staff.data ?? []).map((u: StaffUser) => (
            <tr key={u.id} className={`border-t border-gray-100 ${u.disabled ? "text-gray-400" : ""}`}>
              <td className="py-2">{u.email}</td>
              <td>{u.role}</td>
              <td>
                {u.role === "owner" ? (
                  <span className="text-gray-400">All stores</span>
                ) : (
                  <StoreChecklist
                    stores={allStores}
                    access={{ all_stores: u.all_stores, store_ids: u.store_ids }}
                    disabled={pendingIds.has(u.id)}
                    onChange={(access) => update.mutate({ id: u.id, body: access })}
                  />
                )}
              </td>
              <td className="text-right">
                {u.role === "staff" && (
                  <button
                    className="text-sm underline disabled:opacity-50"
                    disabled={pendingIds.has(u.id)}
                    onClick={() => update.mutate({ id: u.id, body: { disabled: !u.disabled } })}
                  >
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
