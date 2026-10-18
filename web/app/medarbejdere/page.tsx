"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { useMig } from "@/components/Ramme";
import { hent, send } from "@/lib/api";
import { dato, tidspunkt } from "@/lib/format";

type Medarbejder = {
  id: number; navn: string; email: string; rolle: "admin" | "medarbejder"; aktiv: boolean;
  forbundet_til_microsoft: boolean; sidst_logget_ind: string | null;
  tid: Tid;
};
type Sats = { fra: string; til: string | null; normtid: string | null; kostpris_pr_time: string;
  produktive_timer_aar: string; note: string | null };
type Tid = {
  ansaettelse: "fastloennet" | "timeloennet" | null; flexjob: boolean; afspadsering: boolean | null;
  loenref: string | null; sats: Sats | null; naeste_sats: Sats | null;
  tidligere_kostpris: { kostpris_pr_time: string; fra: string } | null;
};

const ANSAETTELSE = { fastloennet: "Fastlønnet", timeloennet: "Timelønnet" };

function tal(v: string): string {
  return Number(v).toLocaleString("da-DK", { maximumFractionDigits: 2 });
}

// Kun dansk skrivemåde: 425 · 425,50 · 1.250,00 – "425.5" afvises, så et tal aldrig gættes.
// Returnerer tallet med punktum (til serveren) eller null, hvis det ikke kan læses.
function danskTal(v: string): string | null {
  const t = v.trim();
  if (!/^\d{1,3}(\.\d{3})*(,\d{1,2})?$|^\d+(,\d{1,2})?$/.test(t)) return null;
  return t.replace(/\./g, "").replace(",", ".");
}

function kr(v: string): string {
  return Number(v).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr.";
}

const ROLLE = { admin: "Administrator", medarbejder: "Medarbejder" };

export default function Side() {
  const mig = useMig();
  const [liste, setListe] = useState<Medarbejder[] | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
  const [aaben, setAaben] = useState<number | null>(null);
  const hentListe = useCallback(() => { hent<Medarbejder[]>("/api/medarbejdere").then(setListe); }, []);
  useEffect(() => { if (mig?.rolle === "admin") hentListe(); }, [mig, hentListe]);

  if (!mig) return <p className="text-slate-500">Henter …</p>;
  if (mig.rolle !== "admin") return <p className="text-slate-600">Kun administratorer kan se medarbejderne.</p>;

  async function udfoer(handling: () => Promise<unknown>, ok: string): Promise<boolean> {
    try { await handling(); setBesked({ tekst: ok }); hentListe(); return true; }
    catch (e) { setBesked({ tekst: e instanceof Error ? e.message : "Det lykkedes ikke", fejl: true }); return false; }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Medarbejdere</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          Kun medarbejdere på listen kan logge ind – med deres egen Microsoft-konto (samme som Outlook). Kontoen
          skal findes i jeres Microsoft 365 og have to-trins-login sat op. Medarbejdere slettes aldrig: luk
          adgangen, når nogen stopper – den lukkes med det samme. Alle ændringer logges med dit navn.
        </p>
      </div>

      <NyMedarbejder gem={(d) => udfoer(() => send("/api/medarbejdere", d), `${d.email} er oprettet og kan nu logge ind`)} />

      {besked && <p className={`rounded-md px-3 py-2 text-sm ${besked.fejl ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{besked.tekst}</p>}

      {!liste ? <p className="text-slate-500">Henter …</p> : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr><th className="px-3 py-2">Navn</th><th className="px-3 py-2">E-mail</th><th className="px-3 py-2">Rolle</th>
                <th className="px-3 py-2">Timeregistrering</th>
                <th className="px-3 py-2">Adgang</th><th className="px-3 py-2">Sidst logget ind</th><th className="px-3 py-2" /></tr>
            </thead>
            <tbody>
              {liste.map((m) => {
                const mig_selv = m.email === mig.email;
                return (
                  <Fragment key={m.id}>
                  <tr className={`border-b border-slate-100 last:border-0 ${m.aktiv ? "" : "text-slate-400"}`}>
                    <td className="px-3 py-2">{m.navn}{mig_selv && <span className="ml-2 text-xs text-slate-400">(dig)</span>}</td>
                    <td className="px-3 py-2">{m.email}</td>
                    <td className="px-3 py-2">
                      <select value={m.rolle} disabled={mig_selv || !m.aktiv}
                              title={mig_selv ? "Du kan ikke ændre din egen rolle" : undefined}
                              onChange={(e) => udfoer(() => send(`/api/medarbejdere/${m.id}`, { rolle: e.target.value }),
                                                      `${m.navn} er nu ${ROLLE[e.target.value as Medarbejder["rolle"]].toLowerCase()}`)}
                              className="rounded border border-slate-300 bg-white px-2 py-1 disabled:opacity-60">
                        <option value="medarbejder">Medarbejder</option>
                        <option value="admin">Administrator</option>
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <TidKort tid={m.tid} />
                      <button type="button" onClick={() => setAaben(aaben === m.id ? null : m.id)}
                              className="text-xs text-brand-600 hover:underline">{aaben === m.id ? "Luk" : "Ret"}</button>
                    </td>
                    <td className="px-3 py-2">
                      {m.aktiv ? <span className="rounded bg-emerald-100 px-1.5 text-xs font-semibold text-emerald-800">Aktiv</span>
                               : <span className="rounded bg-slate-200 px-1.5 text-xs font-semibold text-slate-600">Lukket</span>}
                    </td>
                    <td className="px-3 py-2">{m.sidst_logget_ind ? tidspunkt(m.sidst_logget_ind) : <span className="text-slate-400">aldrig</span>}</td>
                    <td className="px-3 py-2 text-right">
                      {!mig_selv && (m.aktiv ? (
                        <button onClick={() => window.confirm(`Luk adgangen for ${m.navn}? Den lukkes med det samme.`) &&
                                  udfoer(() => send(`/api/medarbejdere/${m.id}`, { aktiv: false }), `Adgangen for ${m.navn} er lukket`)}
                                className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-red-50 hover:text-red-800">Luk adgang</button>
                      ) : (
                        <button onClick={() => udfoer(() => send(`/api/medarbejdere/${m.id}`, { aktiv: true }), `Adgangen for ${m.navn} er genåbnet`)}
                                className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50">Genåbn</button>
                      ))}
                    </td>
                  </tr>
                  {aaben === m.id && (
                    <tr className="border-b border-slate-100 bg-slate-50">
                      <td colSpan={7} className="px-3 py-3">
                        <TidRet m={m} udfoer={udfoer} />
                      </td>
                    </tr>
                  )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Kort oversigt i tabellen: ansættelsesform, normtid og kostpris, der gælder i dag.
function TidKort({ tid }: { tid: Tid }) {
  if (!tid.ansaettelse) return <div className="text-slate-400">ikke sat op</div>;
  return (
    <div className="tabular-nums">
      <div>{ANSAETTELSE[tid.ansaettelse]}{tid.flexjob && " · flexjob"}</div>
      {tid.sats ? (
        <div className="text-xs text-slate-600">
          {tid.sats.normtid ? `${tal(tid.sats.normtid)} t/uge · ` : ""}{kr(tid.sats.kostpris_pr_time)}/t
          <span className="text-slate-400"> fra {dato(tid.sats.fra)}</span>
        </div>
      ) : <div className="text-xs text-slate-400">ingen sats i dag</div>}
      {tid.naeste_sats && (
        <div className="text-xs text-slate-500">→ {kr(tid.naeste_sats.kostpris_pr_time)}/t fra {dato(tid.naeste_sats.fra)}</div>
      )}
    </div>
  );
}

// Ret ansættelsesform og læg en ny sats ind (normtid, kostpris, produktive timer) fra en dato.
// Intet beregnes automatisk – alt tastes. Samme dato som en eksisterende sats retter den.
function TidRet({ m, udfoer }: { m: Medarbejder; udfoer: (h: () => Promise<unknown>, ok: string) => Promise<boolean> }) {
  const [ansaettelse, setAnsaettelse] = useState<string>(m.tid.ansaettelse ?? "");
  const [flexjob, setFlexjob] = useState(m.tid.flexjob);
  const [loenref, setLoenref] = useState(m.tid.loenref ?? "");
  const [fra, setFra] = useState(() => new Date().toISOString().slice(0, 10));
  const [normtid, setNormtid] = useState("");
  const [kostpris, setKostpris] = useState("");
  const [produktive, setProduktive] = useState("");
  const [note, setNote] = useState("");
  const [fejl, setFejl] = useState<string | null>(null);

  async function gemAnsaettelse() {
    if (!ansaettelse) { setFejl("Vælg ansættelsesform"); return; }
    setFejl(null);
    await udfoer(() => send(`/api/medarbejdere/${m.id}/ansaettelse`, { ansaettelse, flexjob, loenref: loenref || null }),
                 `Ansættelsen for ${m.navn} er gemt`);
  }

  async function gemSats() {
    const n = normtid.trim() ? danskTal(normtid) : null;
    const k = danskTal(kostpris);
    const p = danskTal(produktive);
    if (normtid.trim() && n === null) { setFejl("Skriv normtiden med komma som decimaltegn, fx 37 eller 37,5"); return; }
    if (k === null) { setFejl("Skriv kostprisen med komma som decimaltegn, fx 425,50"); return; }
    if (p === null) { setFejl("Skriv de produktive timer pr. år, fx 1.450"); return; }
    if (!fra) { setFejl("Vælg datoen, satsen gælder fra"); return; }
    setFejl(null);
    if (await udfoer(() => send(`/api/medarbejdere/${m.id}/sats`, {
      gyldig_fra: fra, normtid: n, kostpris_pr_time: k, produktive_timer_aar: p, note: note.trim() || null }),
      `Satsen for ${m.navn} er gemt (gælder fra ${dato(fra)})`)) {
      setNormtid(""); setKostpris(""); setProduktive(""); setNote("");
    }
  }

  const felt = "mt-1 rounded border border-slate-300 bg-white px-2 py-1";
  return (
    <div className="space-y-4 text-sm">
      {m.tid.tidligere_kostpris && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-amber-900">
          Tidligere tastet kostpris: {kr(m.tid.tidligere_kostpris.kostpris_pr_time)}/t fra {dato(m.tid.tidligere_kostpris.fra)}.
          Den skal tastes ind igen som en sats nedenfor (sammen med normtid og produktive timer).
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col">Ansættelsesform
          <select value={ansaettelse} onChange={(e) => setAnsaettelse(e.target.value)} className={felt}>
            <option value="">Vælg …</option>
            <option value="fastloennet">Fastlønnet</option>
            <option value="timeloennet">Timelønnet</option>
          </select>
        </label>
        <label className="flex items-center gap-2 pb-1.5">
          <input type="checkbox" checked={flexjob} onChange={(e) => setFlexjob(e.target.checked)} /> Flexjob
        </label>
        <label className="flex flex-col">Lønnummer (Zenergy)
          <input value={loenref} onChange={(e) => setLoenref(e.target.value)} className={`${felt} w-40`} />
        </label>
        <button type="button" onClick={gemAnsaettelse}
                className="rounded bg-brand-600 px-3 py-1.5 text-xs text-white hover:bg-brand-700">Gem ansættelse</button>
        {ansaettelse === "timeloennet" && <span className="pb-1.5 text-xs text-slate-500">Timelønnede optjener ikke afspadsering og kan ikke registrere ferie.</span>}
      </div>

      {m.tid.ansaettelse && (
        <div className="flex flex-wrap items-end gap-3 border-t border-slate-200 pt-3">
          <label className="flex flex-col">Gælder fra
            <input type="date" value={fra} onChange={(e) => setFra(e.target.value)} className={felt} />
          </label>
          <label className="flex flex-col">Normtid (timer/uge)
            <input value={normtid} onChange={(e) => setNormtid(e.target.value)} inputMode="decimal" placeholder="fx 37"
                   className={`${felt} w-28`} />
          </label>
          <label className="flex flex-col">Kostpris pr. time (kr.)
            <input value={kostpris} onChange={(e) => setKostpris(e.target.value)} inputMode="decimal" placeholder="fx 425,50"
                   className={`${felt} w-32`} />
          </label>
          <label className="flex flex-col">Produktive timer pr. år
            <input value={produktive} onChange={(e) => setProduktive(e.target.value)} inputMode="decimal" placeholder="fx 1.450"
                   className={`${felt} w-32`} />
          </label>
          <label className="flex flex-col">Note
            <input value={note} onChange={(e) => setNote(e.target.value)} className={`${felt} w-56`} />
          </label>
          <button type="button" onClick={gemSats}
                  className="rounded bg-brand-600 px-3 py-1.5 text-xs text-white hover:bg-brand-700">Gem sats</button>
        </div>
      )}
      {fejl && <p className="text-xs text-red-700">{fejl}</p>}
      <p className="text-xs text-slate-500">
        Kostprisen er direkte: løn, feriepenge, ATP, pension og sociale omkostninger – intet overhead. En ny sats afslutter
        den forrige; satser slettes aldrig, og alle ændringer logges.
      </p>
    </div>
  );
}

function NyMedarbejder({ gem }: { gem: (d: { email: string; navn: string; rolle: string }) => Promise<boolean> }) {
  const [email, setEmail] = useState("");
  const [navn, setNavn] = useState("");
  const [rolle, setRolle] = useState("medarbejder");
  return (
    <form onSubmit={async (e) => { e.preventDefault(); if (await gem({ email, navn, rolle })) { setEmail(""); setNavn(""); setRolle("medarbejder"); } }}
          className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <label className="flex flex-col">Navn
        <input required value={navn} onChange={(e) => setNavn(e.target.value)} className="mt-1 w-56 rounded border border-slate-300 px-2 py-1.5" />
      </label>
      <label className="flex flex-col">E-mail (Microsoft-konto)
        <input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="navn@dinbogholder.dk"
               className="mt-1 w-72 rounded border border-slate-300 px-2 py-1.5" />
      </label>
      <label className="flex flex-col">Rolle
        <select value={rolle} onChange={(e) => setRolle(e.target.value)} className="mt-1 rounded border border-slate-300 bg-white px-2 py-1.5">
          <option value="medarbejder">Medarbejder</option>
          <option value="admin">Administrator</option>
        </select>
      </label>
      <button className="rounded-md bg-brand-600 px-4 py-1.5 text-white hover:bg-brand-700">Tilføj medarbejder</button>
    </form>
  );
}
