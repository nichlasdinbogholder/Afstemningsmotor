import type { Alvor, Status } from "./api";

const kr = new Intl.NumberFormat("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function beloeb(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "";
  return kr.format(typeof v === "string" ? Number(v) : v);
}

export function dato(iso: string | null | undefined): string {
  if (!iso) return "";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${a}`;
}

export function tidspunkt(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("da-DK", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export const ALVOR: Record<Alvor, string> = { high: "Høj", medium: "Mellem", low: "Lav" };

export const STATUS: Record<Status, string> = {
  open: "Åben", accepted: "Godkendt", resolved: "Løst", ignored: "Ignoreret",
};

export const KILDE: Record<string, string> = {
  grossist: "Grossist", skattekonto: "Skattekonto", bank: "Bank", andet: "Andet",
};
