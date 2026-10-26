"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";
import AdminLaas from "@/components/AdminLaas";

// Kundekortet (Kundeoversigten). Trin 3: hovedet og fanen Kontaktpersoner – de øvrige faner bygges i trin 5.
// Alle regler (hvem må rette, præcis én primær) afgøres på serveren (/api/kundekort).
type Hoved = {
  id: number; navn: string; kundenummer: string; cvr: string | null; status: string; status_kode: string;
  ansvarlig: string | null; ansvarlig_id: number | null; assistent: string | null; assistent_id: number | null;
  kan_rette: boolean; admin: boolean; mangler_aftale: boolean;
};
type Stamdata = {
  navn: string | null; cvr: string | null; virksomhedsform: string | null; status: string | null; hentet: string | null;
  regnskabsperiode: { fra: string; til: string; navn: string } | null; regnskabsperiode_advarsel: string | null;
};
type Valg = { medarbejdere: { id: number; navn: string }[] };
type Kontakt = {
  id: number; navn: string; rolle: string | null; telefon: string | null; email: string | null;
  primaer: boolean; underskriver: boolean; maa_kontaktes_af: "alle" | "ansvarlig"; arkiveret: string | null;
  raadgiver: Raadgiver | null;
};
type Raadgiver = { id: number; type: "revisor" | "bank"; navn: string; firma: string; telefon: string | null; email: string | null };

const FANER = [
  { kode: "stamdata", tekst: "Stamdata" },
  { kode: "kontakter", tekst: "Kontaktpersoner" },
  { kode: "aftale", tekst: "Aftale" },
  { kode: "frister", tekst: "Frister" },
  { kode: "integrationer", tekst: "Integrationer" },
  { kode: "salgsfakturaer", tekst: "Salgsfakturaer" },
  { kode: "dokumenter", tekst: "Dokumenter" },
  { kode: "noter", tekst: "Noter" },
  { kode: "log", tekst: "Log" },
  { kode: "timer", tekst: "Timer" },
];

export default function Side() {
  return (
    <Suspense fallback={<p className="text-slate-500">Henter …</p>}>
      <Kundekort />
    </Suspense>
  );
}

function Kundekort() {
  const params = useSearchParams();
  const id = Number(params.get("id"));
  const fane = params.get("fane") ?? "kontakter";
  const [hoved, setHoved] = useState<Hoved | null>(null);
  const [fejl, setFejl] = useState<string | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const [valg, setValg] = useState<Valg | null>(null);
  const hentHoved = useCallback(() => {
    hent<Hoved>(`/api/kundekort/${id}`).then(setHoved).catch((e) => setFejl(e instanceof Error ? e.message : "Fejl"));
  }, [id]);
  useEffect(() => { hentHoved(); hent<Valg>("/api/kundekartotek/valg").then(setValg).catch(() => {}); }, [hentHoved]);

  // Kun administratorer: status, ansvarlig og assistent sættes her (ikke i Kundeoversigten – bygherren 26.10.2026).
  async function aendr(sti: string, data: unknown, ok: string) {
    try { await send(`/api/kundekartotek/${id}/${sti}`, data); setBesked({ tekst: ok }); hentHoved(); }
    catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); }
  }

  if (fejl) return <p className="text-red-700">{fejl}</p>;
  if (!hoved) return <p className="text-slate-500">Henter …</p>;
  return (
    <div className="space-y-5">
      <div>
        <Link href="/kunder/" className="text-sm text-brand-600 hover:underline">← Kundeoversigt</Link>
        <h1 className="mt-1 text-2xl font-semibold">{hoved.navn}</h1>
        <p className="mt-1 text-sm text-slate-600">
          Kundenr. {hoved.kundenummer}{hoved.cvr && ` · CVR ${hoved.cvr}`}
          {!(hoved.admin && valg && hoved.status_kode !== "opsagt") &&
            ` · ${hoved.status} · Ansvarlig: ${hoved.ansvarlig ?? "ingen"} · Assistent: ${hoved.assistent ?? "ingen"}`}
        </p>
        {hoved.admin && valg && hoved.status_kode !== "opsagt" && (
          <div className="mt-3 flex flex-wrap items-end gap-4 text-sm">
            <span className="flex items-center gap-1 pb-1.5 text-xs text-amber-700"><AdminLaas /> Kun administratorer</span>
            <label className="flex flex-col text-slate-600">Status
              <select value={hoved.status_kode} onChange={(e) => aendr("status", { status: e.target.value }, "Status er ændret")}
                      className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-slate-900">
                <option value="onboarding">Onboarding</option>
                <option value="aktiv">Aktiv</option>
                <option value="pause">Pause</option>
              </select>
            </label>
            <label className="flex flex-col text-slate-600">Ansvarlig
              <select value={hoved.ansvarlig_id ?? ""} className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-slate-900"
                      onChange={(e) => aendr("ansvarlig", { ansvarlig_id: e.target.value ? Number(e.target.value) : null }, "Ansvarlig er ændret")}>
                <option value="">Ingen</option>
                {valg.medarbejdere.map((m) => <option key={m.id} value={m.id}>{m.navn}</option>)}
              </select>
            </label>
            <label className="flex flex-col text-slate-600">Assistent
              <select value={hoved.assistent_id ?? ""} className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-slate-900"
                      onChange={(e) => aendr("assistent", { assistent_id: e.target.value ? Number(e.target.value) : null }, "Assistent er ændret")}>
                <option value="">Ingen</option>
                {valg.medarbejdere.map((m) => <option key={m.id} value={m.id}>{m.navn}</option>)}
              </select>
            </label>
          </div>
        )}
        {besked && <p className={`mt-3 rounded-md px-3 py-2 text-sm ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}
      </div>
      <nav className="flex flex-wrap gap-1 border-b border-slate-200 text-sm">
        {FANER.map((f) => (
          <Link key={f.kode} href={`/kunder/kort/?id=${id}&fane=${f.kode}`}
                className={`-mb-px border-b-2 px-3 py-2 ${fane === f.kode ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900"}`}>
            {f.tekst}
            {f.kode === "aftale" && hoved.mangler_aftale && (
              <span title="Der er ikke uploadet et underskrevet aftalegrundlag" className="ml-1.5 inline-flex align-middle text-red-600">
                <AlarmKlokke />
              </span>
            )}
          </Link>
        ))}
      </nav>
      {fane === "kontakter" ? <Kontakter hoved={hoved} />
        : fane === "stamdata" ? <StamdataFane hoved={hoved} />
        : fane === "aftale" ? <AftaleFane hoved={hoved} opdateret={hentHoved} />
        : <p className="text-sm text-slate-500">Denne fane bygges i et senere trin.</p>}
    </div>
  );
}

function Kontakter({ hoved }: { hoved: Hoved }) {
  const [liste, setListe] = useState<Kontakt[] | null>(null);
  const [visArkiverede, setVisArkiverede] = useState(false);
  const [retter, setRetter] = useState<number | "ny" | RaadgiverType | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const hentListe = useCallback(() => {
    hent<Kontakt[]>(`/api/kundekort/${hoved.id}/kontakter?arkiverede=${visArkiverede}`).then(setListe);
  }, [hoved.id, visArkiverede]);
  useEffect(() => { hentListe(); }, [hentListe]);

  async function udfoer(handling: () => Promise<unknown>, ok: string): Promise<boolean> {
    try { await handling(); setBesked({ tekst: ok }); hentListe(); return true; }
    catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); return false; }
  }
  const tilfoejer = retter === "ny" || retter === "revisor" || retter === "bank";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={visArkiverede} onChange={(e) => setVisArkiverede(e.target.checked)} />
          Vis også arkiverede
        </label>
        {hoved.kan_rette && !tilfoejer && (
          <div className="flex gap-2">
            {(["revisor", "bank"] as const).map((type) => (
              <button key={type} onClick={() => setRetter(type)}
                      className={`flex items-center gap-1.5 rounded-md border bg-white px-4 py-2 text-sm font-medium ${RAADGIVER[type].knap}`}>
                <RaadgiverIkon type={type} />{RAADGIVER[type].tilfoej}
              </button>
            ))}
            <button onClick={() => setRetter("ny")}
                    className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
              Tilføj kontaktperson
            </button>
          </div>
        )}
      </div>
      {retter === "ny" && (
        <KontaktFormular luk={() => setRetter(null)} ny
                         gem={(d) => udfoer(() => send(`/api/kundekort/${hoved.id}/kontakter`, d), `${d.navn} er tilføjet`)} />
      )}
      {(retter === "revisor" || retter === "bank") && (
        <RaadgiverVaelger type={retter} luk={() => setRetter(null)}
                          gem={(d, navn) => udfoer(() => send(`/api/kundekort/${hoved.id}/raadgiver`, d), `${navn} er tilføjet`)} />
      )}
      {besked && <p className={`rounded-md px-3 py-2 text-sm ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}
      {!liste ? <p className="text-slate-500">Henter …</p> : liste.length === 0 ? (
        <p className="text-sm text-slate-500">Ingen kontaktpersoner endnu.</p>
      ) : (
        <ul className="space-y-2">
          {liste.map((k) => k.raadgiver ? (
            <RaadgiverKort key={k.id} k={k} r={k.raadgiver} hoved={hoved} retter={retter === k.id}
                           setRetter={(v) => setRetter(v ? k.id : null)} udfoer={udfoer} />
          ) : (
            <li key={k.id} className={`rounded-lg border border-slate-200 bg-white p-4 text-sm ${k.arkiveret ? "opacity-60" : ""}`}>
              {retter === k.id ? (
                <KontaktFormular start={k} luk={() => setRetter(null)}
                                 gem={(d) => udfoer(() => send(`/api/kundekort/kontakter/${k.id}`, d), `${d.navn} er rettet`)} />
              ) : (
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="font-medium text-slate-900">
                      {k.navn}{k.rolle && <span className="font-normal text-slate-500"> · {k.rolle}</span>}
                    </p>
                    <p className="mt-0.5 text-slate-600">
                      {[k.telefon, k.email].filter(Boolean).join(" · ") || <span className="text-slate-400">Ingen telefon eller e-mail</span>}
                    </p>
                    <Maerker k={k} />
                  </div>
                  {hoved.kan_rette && !k.arkiveret && (
                    <div className="flex shrink-0 gap-2">
                      {!k.primaer && (
                        <button onClick={() => udfoer(() => send(`/api/kundekort/kontakter/${k.id}/primaer`), `${k.navn} er nu primær`)}
                                className="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">Gør til primær</button>
                      )}
                      <button onClick={() => setRetter(k.id)}
                              className="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">Ret</button>
                      <button onClick={() => window.confirm(`Arkivér ${k.navn}? Kontaktpersonen slettes ikke.`) &&
                                        udfoer(() => send(`/api/kundekort/kontakter/${k.id}/arkiver`), `${k.navn} er arkiveret`)}
                              className="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">Arkivér</button>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Maerker({ k }: { k: Kontakt }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
      {k.primaer && <span className="rounded bg-brand-50 px-1.5 py-0.5 font-medium text-brand-700">Primær</span>}
      {k.underskriver && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">Underskriver</span>}
      {k.maa_kontaktes_af === "ansvarlig" && (
        <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-900">Kontaktes kun af den ansvarlige</span>
      )}
      {k.arkiveret && <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">Arkiveret</span>}
    </div>
  );
}

// Revisor (violet) og bankrådgiver (mørkeblå) – bygherren 26.10.2026. Klasserne skrives helt ud, så Tailwind finder dem.
type RaadgiverType = "revisor" | "bank";
const RAADGIVER: Record<RaadgiverType, {
  titel: string; tilfoej: string; firma: string; kant: string; maerke: string; tekst: string; knap: string;
}> = {
  revisor: { titel: "Revisor", tilfoej: "Tilføj revisor", firma: "Revisionsfirma", kant: "border-l-violet-500",
    maerke: "bg-violet-100 text-violet-800", tekst: "text-violet-900",
    knap: "border-violet-300 text-violet-800 hover:bg-violet-50" },
  bank: { titel: "Bankrådgiver", tilfoej: "Tilføj bankrådgiver", firma: "Bank", kant: "border-l-blue-900",
    maerke: "bg-blue-100 text-blue-900", tekst: "text-blue-900",
    knap: "border-blue-300 text-blue-900 hover:bg-blue-50" },
};

function RaadgiverIkon({ type }: { type: RaadgiverType }) {
  return type === "bank" ? (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-3.5 w-3.5">
      <path d="M10 2 3 5v1h14V5l-7-3ZM4 7h2v7H4V7Zm5 0h2v7H9V7Zm5 0h2v7h-2V7ZM3 15h14v2H3v-2Z" />
    </svg>
  ) : (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-3.5 w-3.5">
      <path d="M6 2h8a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm1 3v2h6V5H7Zm0 4v2h2V9H7Zm4 0v2h2V9h-2Zm-4 4v2h2v-2H7Zm4 0v2h2v-2h-2Z" />
    </svg>
  );
}

function RaadgiverKort({ k, r, hoved, retter, setRetter, udfoer }: {
  k: Kontakt; r: Raadgiver; hoved: Hoved; retter: boolean; setRetter: (v: boolean) => void;
  udfoer: (handling: () => Promise<unknown>, ok: string) => Promise<boolean>;
}) {
  const s = RAADGIVER[r.type];
  return (
    <li className={`rounded-lg border border-l-4 border-slate-200 bg-white p-4 text-sm ${s.kant} ${k.arkiveret ? "opacity-60" : ""}`}>
      {retter ? (
        <RaadgiverFormular type={r.type} start={r} luk={() => setRetter(false)}
                           gem={(d) => udfoer(() => send(`/api/raadgivere/${r.id}`, d), `${d.navn} er rettet hos alle kunder`)} />
      ) : (
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 font-medium text-slate-900">
              <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold ${s.maerke}`}>
                <RaadgiverIkon type={r.type} />{s.titel}
              </span>
              {r.navn}
            </p>
            <p className={`mt-0.5 font-medium ${s.tekst}`}>{r.firma}</p>
            <p className="mt-0.5 text-slate-600">
              {[r.telefon, r.email].filter(Boolean).join(" · ") || <span className="text-slate-400">Ingen telefon eller mail</span>}
            </p>
            <Maerker k={k} />
            {/* Åbner en ny mail i mailprogrammet (Outlook) med modtageren udfyldt. */}
            {r.email && !k.arkiveret && (
              <a href={`mailto:${r.email}`}
                 className={`mt-3 inline-flex items-center gap-1.5 rounded-md border bg-white px-3 py-1.5 text-xs font-medium ${s.knap}`}>
                <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-3.5 w-3.5">
                  <path d="M2 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v.4l-8 4.8-8-4.8V5Zm0 2.7V15a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.7l-7.5 4.5a1 1 0 0 1-1 0L2 7.7Z" />
                </svg>
                Send mail i Outlook
              </a>
            )}
          </div>
          {!k.arkiveret && (hoved.admin || hoved.kan_rette) && (
            <div className="flex shrink-0 gap-2">
              {hoved.admin && (
                <button onClick={() => setRetter(true)} title="Rettelsen gælder alle kunder med denne rådgiver"
                        className="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">
                  <AdminLaas className="mr-1" />Ret
                </button>
              )}
              {hoved.kan_rette && (
                <button onClick={() => window.confirm(`Fjern ${r.navn} fra kunden? ${s.titel}en bliver i registret.`) &&
                                  udfoer(() => send(`/api/kundekort/kontakter/${k.id}/arkiver`), `${r.navn} er fjernet fra kunden`)}
                        className="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">Fjern</button>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

type RaadgiverData = { navn: string; firma: string; telefon: string | null; email: string | null };

// Vælg en rådgiver fra det fælles register – eller opret en ny, hvis den ikke findes.
function RaadgiverVaelger({ type, gem, luk }: {
  type: RaadgiverType; gem: (d: unknown, navn: string) => Promise<boolean>; luk: () => void;
}) {
  const s = RAADGIVER[type];
  const [soeg, setSoeg] = useState("");
  const [fund, setFund] = useState<Raadgiver[] | null>(null);
  const [valgt, setValgt] = useState<Raadgiver | null>(null);
  const [opretNy, setOpretNy] = useState(false);
  const [kunAnsvarlig, setKunAnsvarlig] = useState(false);
  useEffect(() => {
    hent<Raadgiver[]>(`/api/raadgivere?type=${type}&q=${encodeURIComponent(soeg)}`).then(setFund);
  }, [type, soeg]);
  const kontaktes = kunAnsvarlig ? "ansvarlig" : "alle";

  if (opretNy) {
    return (
      <RaadgiverFormular type={type} luk={luk} tilbage={() => setOpretNy(false)} kunAnsvarlig={kunAnsvarlig}
                         setKunAnsvarlig={setKunAnsvarlig}
                         gem={(d) => gem({ type, ny: d, maa_kontaktes_af: kontaktes }, d.navn)} />
    );
  }
  return (
    <div className={`rounded-lg border border-l-4 border-slate-200 bg-white p-4 text-sm ${s.kant}`}>
      <p className={`mb-3 flex items-center gap-1.5 font-semibold ${s.tekst}`}><RaadgiverIkon type={type} />Vælg {s.titel.toLowerCase()}</p>
      <input type="search" autoFocus value={soeg} onChange={(e) => { setSoeg(e.target.value); setValgt(null); }}
             placeholder={`Søg på navn eller ${s.firma.toLowerCase()}`}
             className="block w-full rounded-md border border-slate-300 px-3 py-2" />
      <ul className="mt-2 max-h-60 overflow-y-auto rounded-md border border-slate-100">
        {fund === null ? <li className="px-3 py-2 text-slate-500">Henter …</li>
          : fund.length === 0 ? <li className="px-3 py-2 text-slate-500">Ingen {s.titel.toLowerCase()}e fundet</li>
          : fund.map((r) => (
            <li key={r.id}>
              <button type="button" onClick={() => setValgt(r)}
                      className={`flex w-full items-baseline justify-between gap-4 px-3 py-2 text-left hover:bg-slate-50 ${valgt?.id === r.id ? s.maerke : ""}`}>
                <span><span className="font-medium">{r.navn}</span> · {r.firma}</span>
                <span className="text-xs text-slate-500">{[r.telefon, r.email].filter(Boolean).join(" · ")}</span>
              </button>
            </li>
          ))}
      </ul>
      <button type="button" onClick={() => setOpretNy(true)} className={`mt-2 text-sm font-medium underline ${s.tekst}`}>
        Findes {s.titel.toLowerCase()}en ikke? Opret en ny
      </button>
      <label className="mt-3 flex items-center gap-2">
        <input type="checkbox" checked={kunAnsvarlig} onChange={(e) => setKunAnsvarlig(e.target.checked)} /> Kontaktes kun af den ansvarlige
      </label>
      <div className="mt-4 flex gap-2">
        <button type="button" disabled={!valgt} onClick={async () => valgt && (await gem({ type, raadgiver_id: valgt.id, maa_kontaktes_af: kontaktes }, valgt.navn)) && luk()}
                className="rounded-md bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-700 disabled:opacity-40">Tilføj</button>
        <button type="button" onClick={luk} className="rounded-md border border-slate-300 px-4 py-2 hover:bg-slate-50">Annullér</button>
      </div>
    </div>
  );
}

// Ny rådgiver (gemmes i registret) – eller admin retter en eksisterende (gælder alle kunder med rådgiveren).
function RaadgiverFormular({ type, start, gem, luk, tilbage, kunAnsvarlig, setKunAnsvarlig }: {
  type: RaadgiverType; start?: Raadgiver; gem: (d: RaadgiverData) => Promise<boolean>; luk: () => void;
  tilbage?: () => void; kunAnsvarlig?: boolean; setKunAnsvarlig?: (v: boolean) => void;
}) {
  const s = RAADGIVER[type];
  const [navn, setNavn] = useState(start?.navn ?? "");
  const [firma, setFirma] = useState(start?.firma ?? "");
  const [telefon, setTelefon] = useState(start?.telefon ?? "");
  const [email, setEmail] = useState(start?.email ?? "");
  const felt = "mt-1 block w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm";
  async function indsend(e: React.FormEvent) {
    e.preventDefault();
    if (await gem({ navn, firma, telefon: telefon || null, email: email || null })) luk();
  }
  return (
    <form onSubmit={indsend} className={`rounded-lg border border-l-4 border-slate-200 bg-white p-4 text-sm ${s.kant}`}>
      <p className={`mb-3 flex items-center gap-1.5 font-semibold ${s.tekst}`}>
        <RaadgiverIkon type={type} />{start ? `Ret ${s.titel.toLowerCase()}` : `Ny ${s.titel.toLowerCase()}`}
        {start && <span className="font-normal text-slate-500">– rettelsen gælder alle kunder med denne {s.titel.toLowerCase()} <AdminLaas /></span>}
      </p>
      <div className="grid grid-cols-4 gap-4">
        <label>Navn<input required value={navn} onChange={(e) => setNavn(e.target.value)} className={felt} /></label>
        <label>{s.firma}<input required value={firma} onChange={(e) => setFirma(e.target.value)} className={felt} /></label>
        <label>Telefon<input value={telefon} onChange={(e) => setTelefon(e.target.value)} className={felt} /></label>
        <label>Mailadresse<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={felt} /></label>
      </div>
      {setKunAnsvarlig && (
        <label className="mt-3 flex items-center gap-2">
          <input type="checkbox" checked={kunAnsvarlig} onChange={(e) => setKunAnsvarlig(e.target.checked)} /> Kontaktes kun af den ansvarlige
        </label>
      )}
      <div className="mt-4 flex gap-2">
        <button type="submit" className="rounded-md bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-700">Gem</button>
        {tilbage && <button type="button" onClick={tilbage} className="rounded-md border border-slate-300 px-4 py-2 hover:bg-slate-50">Tilbage til listen</button>}
        <button type="button" onClick={luk} className="rounded-md border border-slate-300 px-4 py-2 hover:bg-slate-50">Annullér</button>
      </div>
    </form>
  );
}

type Data = { navn: string; rolle: string | null; telefon: string | null; email: string | null; underskriver: boolean;
  maa_kontaktes_af: string; primaer?: boolean };

function KontaktFormular({ start, ny, gem, luk }: {
  start?: Kontakt; ny?: boolean; gem: (d: Data) => Promise<boolean>; luk: () => void;
}) {
  const [navn, setNavn] = useState(start?.navn ?? "");
  const [rolle, setRolle] = useState(start?.rolle ?? "");
  const [telefon, setTelefon] = useState(start?.telefon ?? "");
  const [email, setEmail] = useState(start?.email ?? "");
  const [underskriver, setUnderskriver] = useState(start?.underskriver ?? false);
  const [kunAnsvarlig, setKunAnsvarlig] = useState(start?.maa_kontaktes_af === "ansvarlig");
  const [primaer, setPrimaer] = useState(false);

  async function indsend(e: React.FormEvent) {
    e.preventDefault();
    const d: Data = { navn, rolle: rolle || null, telefon: telefon || null, email: email || null, underskriver,
      maa_kontaktes_af: kunAnsvarlig ? "ansvarlig" : "alle", ...(ny ? { primaer } : {}) };
    if (await gem(d)) luk();
  }

  const felt = "mt-1 block w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm";
  return (
    <form onSubmit={indsend} className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <div className="grid grid-cols-4 gap-4">
        <label>Navn<input required value={navn} onChange={(e) => setNavn(e.target.value)} className={felt} /></label>
        <label>Rolle<input value={rolle} onChange={(e) => setRolle(e.target.value)} placeholder="fx Direktør" className={felt} /></label>
        <label>Telefon<input value={telefon} onChange={(e) => setTelefon(e.target.value)} className={felt} /></label>
        <label>E-mail<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={felt} /></label>
      </div>
      <div className="mt-3 flex flex-wrap gap-5">
        <label className="flex items-center gap-2"><input type="checkbox" checked={underskriver} onChange={(e) => setUnderskriver(e.target.checked)} /> Underskriver</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={kunAnsvarlig} onChange={(e) => setKunAnsvarlig(e.target.checked)} /> Kontaktes kun af den ansvarlige</label>
        {ny && <label className="flex items-center gap-2"><input type="checkbox" checked={primaer} onChange={(e) => setPrimaer(e.target.checked)} /> Primær kontaktperson</label>}
      </div>
      <div className="mt-4 flex gap-2">
        <button type="submit" className="rounded-md bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-700">Gem</button>
        <button type="button" onClick={luk} className="rounded-md border border-slate-300 px-4 py-2 hover:bg-slate-50">Annullér</button>
      </div>
    </form>
  );
}

// Stamdata (bygherren 26.10.2026): hentes automatisk – navn og virksomhedsform fra CVR, regnskabsperioden fra kundens
// regnskabssystem. Intet tastes her. Afvigelser mellem CVR og regnskabssystemet regnes på serveren.
function StamdataFane({ hoved }: { hoved: Hoved }) {
  const [d, setD] = useState<Stamdata | null>(null);
  useEffect(() => { hent<Stamdata>(`/api/kundekort/${hoved.id}/stamdata`).then(setD); }, [hoved.id]);
  if (!d) return <p className="text-slate-500">Henter …</p>;
  const dato = (iso: string) => new Date(iso).toLocaleDateString("da-DK");
  const tom = <span className="text-slate-400">–</span>;
  const felter: [string, React.ReactNode, string][] = [
    ["Virksomhedens navn", d.navn ?? tom, "CVR"],
    ["CVR-nummer", d.cvr ?? tom, "Kundekartoteket"],
    ["Virksomhedsform", d.virksomhedsform ?? tom, "CVR"],
    ["Regnskabsperiode", d.regnskabsperiode ? `${dato(d.regnskabsperiode.fra)} – ${dato(d.regnskabsperiode.til)}` : tom,
     "Regnskabssystemet"],
  ];
  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-600">
        {d.hentet ? `Hentet automatisk fra CVR ${dato(d.hentet)}.`
          : hoved.cvr ? "Ikke hentet fra CVR endnu – det sker automatisk hver morgen kl. 6."
          : "Kunden har intet CVR-nummer, så der kan ikke hentes stamdata fra CVR."}
        {" "}Regnskabsperioden er det regnskabsår i kundens regnskabssystem, der gælder i dag.
      </p>
      {d.regnskabsperiode_advarsel && (
        <p className="rounded-md bg-amber-50 px-3 py-2 font-medium text-amber-900">{d.regnskabsperiode_advarsel}</p>
      )}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 rounded-lg border border-slate-200 bg-white p-4">
        {felter.map(([tekst, vaerdi, kilde]) => (
          <div key={tekst}>
            <dt className="text-xs font-medium text-slate-500">{tekst} <span className="font-normal text-slate-400">· {kilde}</span></dt>
            <dd className="mt-0.5 text-base text-slate-900">{vaerdi}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function AlarmKlokke() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-label="Mangler" className="h-4 w-4">
      <path d="M10 2a5 5 0 0 0-5 5v2.6l-1.4 2.8A1 1 0 0 0 4.5 14h11a1 1 0 0 0 .9-1.6L15 9.6V7a5 5 0 0 0-5-5Zm-2 13a2 2 0 0 0 4 0H8Z" />
    </svg>
  );
}

type AftaleDok = { id: number; filnavn: string; stoerrelse: number; uploadet: string; uploadet_af: string | null; arkiveret: string | null };
type AftaleSvar = { gaeldende: AftaleDok | null; tidligere: AftaleDok[]; mangler: boolean; kan_uploade: boolean };

// Aftalegrundlaget (bygherren 26.10.2026): den underskrevne aftale som PDF. Mangler den, er der en alarmklokke ved fanen.
function AftaleFane({ hoved, opdateret }: { hoved: Hoved; opdateret: () => void }) {
  const [d, setD] = useState<AftaleSvar | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const [sender, setSender] = useState(false);
  const hentData = useCallback(() => { hent<AftaleSvar>(`/api/kundekort/${hoved.id}/aftale`).then(setD); }, [hoved.id]);
  useEffect(() => { hentData(); }, [hentData]);

  async function upload(fil: File) {
    setSender(true); setBesked(null);
    try {
      const r = await fetch(`/api/kundekort/${hoved.id}/aftale`, {
        method: "POST", credentials: "same-origin", body: fil,
        headers: { "Content-Type": "application/pdf", "X-Afstemning": "1", "X-Filnavn": encodeURIComponent(fil.name) },
      });
      if (!r.ok) {
        let tekst = `Fejl ${r.status}`;
        try { const j = await r.json(); if (typeof j.detail === "string") tekst = j.detail; } catch {}
        throw new Error(tekst);
      }
      setBesked({ tekst: `${fil.name} er uploadet` }); hentData(); opdateret();
    } catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); }
    finally { setSender(false); }
  }

  if (!d) return <p className="text-slate-500">Henter …</p>;
  const dato = (iso: string) => new Date(iso).toLocaleDateString("da-DK");
  const pdf = (dok: AftaleDok) => `/api/kundekort/${hoved.id}/aftale/${dok.id}/pdf`;
  const knap = d.kan_uploade && (
    <label className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md px-4 py-2 text-sm font-medium text-white ${sender ? "bg-slate-400" : "bg-brand-600 hover:bg-brand-700"}`}>
      {sender ? "Uploader …" : d.gaeldende ? "Upload ny version (PDF)" : "Upload aftalegrundlag (PDF)"}
      <input type="file" accept="application/pdf,.pdf" className="hidden" disabled={sender}
             onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
    </label>
  );

  return (
    <div className="space-y-4 text-sm">
      {besked && <p className={`rounded-md px-3 py-2 ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}
      {d.gaeldende ? (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-white p-4">
          <div>
            <p className="text-xs font-medium text-slate-500">Underskrevet aftalegrundlag</p>
            <a href={pdf(d.gaeldende)} target="_blank" rel="noreferrer" className="mt-0.5 block text-base font-medium text-brand-700 hover:underline">
              {d.gaeldende.filnavn}
            </a>
            <p className="mt-0.5 text-slate-500">Uploadet {dato(d.gaeldende.uploadet)} af {d.gaeldende.uploadet_af ?? "ukendt"}</p>
          </div>
          <div className="flex shrink-0 gap-2">
            <a href={pdf(d.gaeldende)} target="_blank" rel="noreferrer"
               className="rounded-md border border-slate-300 px-4 py-2 font-medium hover:bg-slate-50">Åbn PDF</a>
            {knap}
          </div>
        </div>
      ) : d.mangler ? (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="flex items-center gap-2 font-medium text-red-800">
            <AlarmKlokke />Der er ikke uploadet et underskrevet aftalegrundlag for kunden.
          </p>
          {knap}
        </div>
      ) : (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-slate-600">Intern kunde – der kræves ikke et aftalegrundlag.</p>
          {knap}
        </div>
      )}
      {!d.kan_uploade && <p className="text-slate-500">Kun kundens ansvarlige, assistent eller en administrator kan uploade.</p>}
      {d.tidligere.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-slate-500">Tidligere versioner</p>
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {d.tidligere.map((dok) => (
              <li key={dok.id} className="flex justify-between px-4 py-2">
                <a href={pdf(dok)} target="_blank" rel="noreferrer" className="text-slate-700 hover:underline">{dok.filnavn}</a>
                <span className="text-slate-500">Uploadet {dato(dok.uploadet)} af {dok.uploadet_af ?? "ukendt"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
