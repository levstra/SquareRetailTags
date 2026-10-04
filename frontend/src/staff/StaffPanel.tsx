import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema.d.ts";

type StaffUser = components["schemas"]["StaffUser"];
type Store = components["schemas"]["Store"];
type StaffUpdate = components["schemas"]["StaffUpdate"];

function message(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") {
    return error.detail;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function StoreChecklist({
  stores,
  selected,
  onChange,
  disabled = false,
}: {
  stores: Store[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  // No selected stores means every store, so "All stores" is its own explicit
  // choice and the last specific store can't be unchecked into it by accident.
  const all = selected.length === 0;
  return (
    <div className="flex flex-wrap gap-3">
      <label className="flex items-center gap-1 text-sm">
        <input
          type="checkbox"
          checked={all}
          disabled={disabled}
          onChange={(e) => {
            if (e.target.checked) onChange([]);
          }}
        />
        All stores
      </label>
      {stores.map((s) => (
        <label key={s.id} className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={selected.includes(s.id)}
            disabled={disabled}
            onChange={(e) => {
              if (e.target.checked) onChange([...selected, s.id]);
              else if (selected.length > 1) onChange(selected.filter((id) => id !== s.id));
            }}
          />
          {s.name}
        </label>
      ))}
    </div>
  );
}

export function StaffPanel() {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [inviteStores, setInviteStores] = useState<string[]>([]);
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
      const { error } = await api.POST("/staff", { body: { email, store_ids: inviteStores } });
      if (error) throw error;
    },
    onSuccess: () => {
      setEmail("");
      setInviteStores([]);
      setError("");
      void refresh();
    },
    onError: (e) => setError(message(e)),
  });

  const update = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: StaffUpdate }) => {
      const { error } = await api.PATCH("/staff/{user_id}", { params: { path: { user_id: id } }, body });
      if (error) throw error;
    },
    onSuccess: () => {
      setError("");
      void refresh();
    },
    onError: (e) => setError(message(e)),
  });

  const allStores = stores.data ?? [];
  const pendingId = update.isPending ? update.variables?.id : undefined;

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
        <StoreChecklist stores={allStores} selected={inviteStores} onChange={setInviteStores} />
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
                    selected={u.store_ids}
                    disabled={pendingId === u.id}
                    onChange={(ids) => update.mutate({ id: u.id, body: { store_ids: ids } })}
                  />
                )}
              </td>
              <td className="text-right">
                {u.role === "staff" && (
                  <button
                    className="text-sm underline disabled:opacity-50"
                    disabled={pendingId === u.id}
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
