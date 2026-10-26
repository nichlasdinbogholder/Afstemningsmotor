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
  kan_rette: boolean; admin: boolean;
};
type Stamdata = {
  navn: string | null; cvr: string | null; virksomhedsform: string | null; status: string | null; hentet: string | null;
  regnskabsperiode: { fra: string; til: string; navn: string } | null; regnskabsperiode_advarsel: string | null;
};
type Valg = { medarbejdere: { id: number; navn: string }[] };
type Kontakt = {
  id: number; navn: string; rolle: string | null; telefon: string | null; email: string | null;
  primaer: boolean; underskriver: boolean; maa_kontaktes_af: "alle" | "ansvarlig"; arkiveret: string | null;
};

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
          </Link>
        ))}
      </nav>
      {fane === "kontakter" ? <Kontakter hoved={hoved} />
        : fane === "stamdata" ? <StamdataFane hoved={hoved} />
        : <p className="text-sm text-slate-500">Denne fane bygges i et senere trin.</p>}
    </div>
  );
}

function Kontakter({ hoved }: { hoved: Hoved }) {
  const [liste, setListe] = useState<Kontakt[] | null>(null);
  const [visArkiverede, setVisArkiverede] = useState(false);
  const [retter, setRetter] = useState<number | "ny" | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const hentListe = useCallback(() => {
    hent<Kontakt[]>(`/api/kundekort/${hoved.id}/kontakter?arkiverede=${visArkiverede}`).then(setListe);
  }, [hoved.id, visArkiverede]);
  useEffect(() => { hentListe(); }, [hentListe]);

  async function udfoer(handling: () => Promise<unknown>, ok: string): Promise<boolean> {
    try { await handling(); setBesked({ tekst: ok }); hentListe(); return true; }
    catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); return false; }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={visArkiverede} onChange={(e) => setVisArkiverede(e.target.checked)} />
          Vis også arkiverede
        </label>
        {hoved.kan_rette && retter !== "ny" && (
          <button onClick={() => setRetter("ny")}
                  className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Tilføj kontaktperson
          </button>
        )}
      </div>
      {retter === "ny" && (
        <KontaktFormular luk={() => setRetter(null)} ny
                         gem={(d) => udfoer(() => send(`/api/kundekort/${hoved.id}/kontakter`, d), `${d.navn} er tilføjet`)} />
      )}
      {besked && <p className={`rounded-md px-3 py-2 text-sm ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}
      {!liste ? <p className="text-slate-500">Henter …</p> : liste.length === 0 ? (
        <p className="text-sm text-slate-500">Ingen kontaktpersoner endnu.</p>
      ) : (
        <ul className="space-y-2">
          {liste.map((k) => (
            <li key={k.id} className={`rounded-lg border bg-white p-4 text-sm ${k.arkiveret ? "border-slate-200 opacity-60" : "border-slate-200"}`}>
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
                    <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
                      {k.primaer && <span className="rounded bg-brand-50 px-1.5 py-0.5 font-medium text-brand-700">Primær</span>}
                      {k.underskriver && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">Underskriver</span>}
                      {k.maa_kontaktes_af === "ansvarlig" && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-900">Kontaktes kun af den ansvarlige</span>
                      )}
                      {k.arkiveret && <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-600">Arkiveret</span>}
                    </div>
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
