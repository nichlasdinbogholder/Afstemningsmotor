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

export const BETALINGSSTATUS: Record<string, { tekst: string; farve: string }> = {
  betalt: { tekst: "Betalt", farve: "bg-emerald-600 text-white" },
  ikke_betalt: { tekst: "Ikke betalt", farve: "bg-slate-400 text-white" },
  forfaldet: { tekst: "Forfaldet", farve: "bg-amber-500 text-white" },
  delvist_betalt: { tekst: "Delvist betalt", farve: "bg-sky-600 text-white" },
  kreditnota: { tekst: "Kreditnota", farve: "bg-emerald-300 text-emerald-900" },
  krediteret: { tekst: "Krediteret", farve: "bg-slate-300 text-slate-800" },
  afskrevet: { tekst: "Afskrevet", farve: "bg-slate-300 text-slate-800" },
};

export const KANAL: Record<string, string> = { email: "E-mail", ean: "EAN", print: "Print", eboks: "e-Boks" };

export const SPAERRE: Record<string, string> = {
  betalt: "Betalt", krediteret: "Krediteret", afskrevet: "Afskrevet", kreditnota: "Kreditnota",
  ikke_forfalden: "Ikke forfalden", indbetaling_seneste_2_bankdage: "Indbetaling de seneste 2 bankdage",
  indbetaling_i_kassekladde: "Indbetaling ligger i kassekladden", debitor_blokeret: "Debitor blokeret",
  afbetalingsordning: "Aktiv afbetalingsordning", under_minimumsbeloeb: "Restbeløb under minimum",
  under_10_dage: "For tidligt (10-dagesreglen/plan)", max_3_rykkere: "Har allerede 3 rykkere",
  aktiv_inkassosag: "Aktiv inkassosag", fjernet_af_medarbejder: "Fjernet af medarbejder",
  rykker_i_koe: "Rykker ligger allerede i kø",
};

export const RYKKERTILSTAND: Record<string, string> = {
  off: "Slået fra", preview: "Prøvekørsel", live: "I drift",
};
