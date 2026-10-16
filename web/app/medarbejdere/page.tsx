"use client";

import { useCallback, useEffect, useState } from "react";
import { useMig } from "@/components/Ramme";
import { hent, send } from "@/lib/api";
import { tidspunkt } from "@/lib/format";

type Medarbejder = {
  id: number; navn: string; email: string; rolle: "admin" | "medarbejder"; aktiv: boolean;
  forbundet_til_microsoft: boolean; sidst_logget_ind: string | null;
};

const ROLLE = { admin: "Administrator", medarbejder: "Medarbejder" };

export default function Side() {
  const mig = useMig();
  const [liste, setListe] = useState<Medarbejder[] | null>(null);
  const [besked, setBesked] = useState<{ tekst: string; fejl?: boolean } | null>(null);
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
                <th className="px-3 py-2">Adgang</th><th className="px-3 py-2">Sidst logget ind</th><th className="px-3 py-2" /></tr>
            </thead>
            <tbody>
              {liste.map((m) => {
                const mig_selv = m.email === mig.email;
                return (
                  <tr key={m.id} className={`border-b border-slate-100 last:border-0 ${m.aktiv ? "" : "text-slate-400"}`}>
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
                );
              })}
            </tbody>
          </table>
        </div>
      )}
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
