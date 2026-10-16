// Al data kommer fra Python-delen (FastAPI) på samme adresse. Login-cookien sendes automatisk.
// Ændringer sender headeren X-Afstemning, som FastAPI kræver (beskytter mod falske formularer udefra).

export class IkkeLoggetInd extends Error {}

export class ApiFejl extends Error {
  constructor(public status: number, besked: string) {
    super(besked);
  }
}

async function svar<T>(r: Response): Promise<T> {
  if (r.status === 401) throw new IkkeLoggetInd();
  if (!r.ok) {
    let besked = `Fejl ${r.status}`;
    try {
      const d = await r.json();
      if (typeof d.detail === "string") besked = d.detail;
      else if (r.status === 422) besked = "En af værdierne er ikke gyldig – tjek feltet og prøv igen";
    } catch {}
    throw new ApiFejl(r.status, besked);
  }
  return r.json() as Promise<T>;
}

export async function hent<T>(sti: string): Promise<T> {
  return svar<T>(await fetch(sti, { credentials: "same-origin", cache: "no-store" }));
}

export async function send<T>(sti: string, data?: unknown): Promise<T> {
  return svar<T>(
    await fetch(sti, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Afstemning": "1" },
      body: data === undefined ? undefined : JSON.stringify(data),
    }),
  );
}

// --- Typer (som FastAPI svarer) ---------------------------------------------------------

export type Alvor = "high" | "medium" | "low";
export type Status = "open" | "accepted" | "resolved" | "ignored";

export interface Medarbejder {
  id: number;
  navn: string;
  email: string;
  rolle: "admin" | "medarbejder";
}

export interface KundeRaekke {
  id: number;
  navn: string;
  kundenummer: string;
  cvr: string | null;
  status: string;
  system: string | null;
  aabne_fund: Record<Alvor, number>;
  natkoersel: { tidspunkt: string; status: string } | null;
}

export interface Kunde {
  id: number;
  navn: string;
  kundenummer: string;
  cvr: string | null;
  status: string;
  system: string | null;
  kontoudtog_fra: string | null;
  sidst_opdateret: { tidspunkt: string; status: string; hvordan: string } | null;
}

export interface Fund {
  id: number;
  regel: string;
  regel_navn: string;
  alvor: Alvor;
  status: Status;
  titel: string;
  aktuel: boolean;
  periode_fra: string | null;
  periode_til: string | null;
  foerst_set: string;
  sidst_set: string;
}

export interface FundDetalje extends Fund {
  client_id: number;
  detaljer: Record<string, unknown>;
  historik: { fra: string | null; til: string; af: string; note: string | null; tidspunkt: string }[];
}

export interface Kontoudtog {
  id: number;
  kilde: string;
  kontonummer: number | null;
  modpart: string | null;
  modpart_navn: string | null;
  periode_fra: string;
  periode_til: string;
  kildefil: string | null;
  indlaest: string;
  linjer: number;
  matchet: number;
  matchprocent: number | null;
  sum_linjer: string | null;
}

export interface UdtogLinje {
  nr: number;
  dato: string;
  reference: string | null;
  tekst: string | null;
  beloeb: string;
  match: "bilag_beloeb" | "beloeb_dato" | null;
  bogfoert: { dato: string | null; bilag: number | null; beloeb: string | null } | null;
}

export interface Job {
  id: number;
  status: "koe" | "i_gang" | "faerdig" | "fejlet";
}
