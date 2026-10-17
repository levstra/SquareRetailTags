// Money arrives as integer cents; this is the render boundary.
export function formatPrice(cents: number | null, currency: string | null): string {
  if (cents == null) return "Variable price";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: currency ?? "USD" }).format(cents / 100);
}
