// Kundekartoteket (/api/kundekartotek) – fælles for Kundeoversigt og Arkiverede kunder.
import { beloeb } from "./format";

export type KartotekKunde = {
  id: number; navn: string; kundenummer: string; cvr: string | null; status: "aktiv" | "pause" | "opsagt";
  system: string; ansvarlig: string | null; ansvarlig_id: number | null; startdato: string | null;
  afregning: "abonnement" | "timeafregning" | null; abonnementspris: string | null;
  ophoersdato: string | null; aarsag: string | null; arkiveret_af: string | null; kun_admin: boolean;
};
export type Valg = {
  medarbejdere: { id: number; navn: string }[];
  systemer: { kode: string; navn: string }[];
  aarsager: { kode: string; navn: string }[];
};

export function afregningTekst(k: KartotekKunde): string {
  if (k.afregning === "abonnement") return `Abonnement · ${beloeb(k.abonnementspris)} kr.`;
  if (k.afregning === "timeafregning") return "Timeafregning";
  return "";
}
