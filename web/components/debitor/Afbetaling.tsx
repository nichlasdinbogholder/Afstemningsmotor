"use client";

import { useEffect, useState } from "react";
import { hent } from "@/lib/api";
import { beloeb, dato } from "@/lib/format";

type Ordning = { id: number; fakturanummer: string; debitornummer: string; debitor: string; beloeb: string; rater: number;
                 foerste: string; interval_dage: number; status: string; oprettet: string };
type Svar = { antal: Record<string, number>; ordninger: Ordning[] };

const STATUS: Record<string, string> = { active: "Aktiv", completed: "Gennemført", defaulted: "Misligholdt", cancelled: "Annulleret" };

export default function Afbetaling({ kundeId }: { kundeId: number }) {
  const [filter, setFilter] = useState("alle");
  const [soeg, setSoeg] = useState("");
  const [aktivSoeg, setAktivSoeg] = useState("");
  const [svar, setSvar] = useState<Svar | null>(null);
  useEffect(() => {
    const p = new URLSearchParams({ filter, ...(aktivSoeg ? { q: aktivSoeg } : {}) });
    hent<Svar>(`/api/opkraevning/${kundeId}/afbetalinger?${p}`).then(setSvar);
  }, [kundeId, filter, aktivSoeg]);

  const knap = (id: string, tekst: string, farve?: string) => (
    <button onClick={() => setFilter(id)}
            className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ${filter === id ? "bg-slate-200 font-medium text-slate-900" : "text-sky-700 hover:bg-slate-100"}`}>
      {farve && <span className={`rounded-full px-2 text-xs font-semibold text-white ${farve}`}>{svar?.antal[id] ?? 0}</span>}{tekst}
    </button>
  );

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <form onSubmit={(e) => { e.preventDefault(); setAktivSoeg(soeg); }} className="flex">
            <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg fakturanr. eller kunde"
                   className="w-64 rounded-l-md border border-slate-300 bg-white px-3 py-1.5 text-sm" />
            <button className="rounded-r-md bg-sky-700 px-3 text-sm text-white hover:bg-sky-800">Søg</button>
          </form>
          {knap("aktive", "Aktive", "bg-emerald-600")}
          {knap("misligholdt", "Misligholdt", "bg-red-600")}
          {knap("alle", "Alle")}
        </div>
        <button disabled title="Kommer i trin 5" className="cursor-not-allowed rounded-md bg-emerald-600/40 px-4 py-1.5 text-sm text-white">Opret afbetaling</button>
      </div>
      {!svar ? <p className="text-slate-500">Henter …</p> : svar.ordninger.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Ingen afbetalingsordninger. Oprettelse kommer i trin 5: en medarbejder opretter en ordning på en faktura,
          systemet danner raterne, og rykkerne stopper, så længe ordningen overholdes.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr><th className="px-3 py-2">Kundenr.</th><th className="px-3 py-2">Kunde</th><th className="px-3 py-2">Fakturanr.</th>
                <th className="px-3 py-2">Oprettet</th><th className="px-3 py-2">Første rate</th><th className="px-3 py-2">Rater</th>
                <th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Beløb</th></tr>
            </thead>
            <tbody>
              {svar.ordninger.map((o) => (
                <tr key={o.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-3 py-2">{o.debitornummer}</td><td className="px-3 py-2">{o.debitor}</td>
                  <td className="px-3 py-2">{o.fakturanummer}</td><td className="px-3 py-2">{dato(o.oprettet)}</td>
                  <td className="px-3 py-2">{dato(o.foerste)}</td><td className="px-3 py-2">{o.rater} × hver {o.interval_dage}. dag</td>
                  <td className="px-3 py-2">{STATUS[o.status] ?? o.status}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{beloeb(o.beloeb)} kr.</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
