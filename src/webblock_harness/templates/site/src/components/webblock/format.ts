export type Currency = "USD" | "KHR";

export function formatPrice(price: number | undefined, currency: Currency = "USD"): string {
  if (price === undefined || price === null || Number.isNaN(Number(price))) return "";
  if (currency === "KHR") return `${Math.round(Number(price)).toLocaleString("en-US")}៛`;
  return `$${Number(price).toFixed(2)}`;
}

export function cx(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}
