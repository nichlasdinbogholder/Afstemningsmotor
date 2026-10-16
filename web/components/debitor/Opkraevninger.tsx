"use client";

import { useEffect, useState } from "react";
import { hent } from "@/lib/api";
import { beloeb, BETALINGSSTATUS, dato, KANAL, SENDSTATUS } from "@/lib/format";

type Faktura = {
  id: number; debitornummer: string; debitor: string; fakturanummer: string; oprettet: string; forfald: string;
  beloeb: string; restbeloeb: string; betalingsstatus: string; kanal: string | null; sendstatus: string | null; rykkere: number;
};
type Svar = { antal: Record<string, number>; fakturaer: Faktura[]; afkortet: boolean };

const FILTRE: { id: string; tekst: string; advarsel?: boolean }[] = [
  { id: "forfaldet", tekst: "Forfaldet" }, { id: "ikke_betalt", tekst: "Ikke betalt" },
  { id: "delvist_betalt", tekst: "Delvist betalt" }, { id: "med_rykker", tekst: "Med rykker" },
  { id: "betalt", tekst: "Betalt" }, { id: "kreditnota", tekst: "Kreditnota" },
  { id: "ingen_kanal", tekst: "Ingen kanal", advarsel: true }, { id: "alt", tekst: "Alt" },
];

export default function Opkraevninger({ kundeId, soegning, filterStart, vaelg }: {
  kundeId: number; soegning: string; filterStart: string; vaelg: (id: number, filter: string, q: string) => void;
}) {
  const [filter, setFilter] = useState(filterStart);
  const [soeg, setSoeg] = useState(soegning);
  const [aktivSoeg, setAktivSoeg] = useState(soegning);
  const [svar, setSvar] = useState<Svar | null>(null);
  const params = new URLSearchParams({ filter, ...(aktivSoeg ? { q: aktivSoeg } : {}) });

  useEffect(() => {
    const p = new URLSearchParams({ filter, ...(aktivSoeg ? { q: aktivSoeg } : {}) });
    hent<Svar>(`/api/opkraevning/${kundeId}/fakturaer?${p}`).then(setSvar);
  }, [kundeId, filter, aktivSoeg]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <form onSubmit={(e) => { e.preventDefault(); setAktivSoeg(soeg); }} className="flex">
          <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg fakturanr., kunde eller kundenr."
                 className="w-72 rounded-l-md border border-slate-300 bg-white px-3 py-1.5 text-sm" />
          <button className="rounded-r-md bg-sky-700 px-3 text-sm text-white hover:bg-sky-800" aria-label="Søg">Søg</button>
        </form>
        <a href={`/api/opkraevning/${kundeId}/eksport/fakturaer.csv?${params}`}
           title="Henter de viste fakturaer som en fil til Excel. Eksporten logges."
           className="rounded-md bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-800">Excel-eksport</a>
      </div>
      <div className="mb-3 flex flex-wrap gap-1 border-b border-slate-200 pb-2">
        {FILTRE.map((f) => {
          const n = svar?.antal[f.id] ?? 0;
          return (
            <button key={f.id} onClick={() => setFilter(f.id)}
                    className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ${filter === f.id ? "bg-slate-200 font-medium text-slate-900" : "text-sky-700 hover:bg-slate-100"}`}>
              {f.advarsel && n > 0 && <span className="rounded-full bg-red-600 px-2 text-xs font-semibold text-white">{n}</span>}
              {f.tekst}{!f.advarsel && <span className="text-xs text-slate-400">{n}</span>}
            </button>
          );
        })}
      </div>
      {!svar ? <p className="text-slate-500">Henter fakturaer …</p> : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr>
                <th className="px-2 py-2" /><th className="px-2 py-2">Kundenr.</th><th className="px-2 py-2">Kunde</th>
                <th className="px-2 py-2">Fakturanr.</th><th className="px-2 py-2">Oprettet</th><th className="px-2 py-2">Betalingsdag</th>
                <th className="px-2 py-2">Betalingsstatus</th><th className="px-2 py-2">Sendstatus</th>
                <th className="px-2 py-2">Kanal</th>
                <th className="px-2 py-2 text-right">Fakturabeløb</th>
              </tr>
            </thead>
            <tbody>
              {svar.fakturaer.map((f) => {
                const s = BETALINGSSTATUS[f.betalingsstatus] ?? { tekst: f.betalingsstatus, farve: "bg-slate-200" };
                const ss = f.sendstatus ? SENDSTATUS[f.sendstatus] : null;
                return (
                  <tr key={f.id} onClick={() => vaelg(f.id, filter, aktivSoeg)} className="cursor-pointer whitespace-nowrap border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-2 py-2"><span className="rounded bg-sky-800 px-1.5 py-0.5 text-xs font-semibold text-white">+71</span></td>
                    <td className="px-2 py-2">{f.debitornummer}</td>
                    <td className="min-w-36 whitespace-normal px-2 py-2">{f.debitor}</td>
                    <td className="px-2 py-2">{f.fakturanummer}</td>
                    <td className="px-2 py-2">{dato(f.oprettet)}</td>
                    <td className="px-2 py-2">{dato(f.forfald)}</td>
                    <td className="px-2 py-2"><span className={`inline-block w-28 rounded px-1 py-0.5 text-center text-xs font-semibold ${s.farve}`}>{s.tekst}</span>
                      {f.rykkere > 0 && <span title="Antal sendte rykkere" className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-900">R{f.rykkere}</span>}</td>
                    <td className="px-2 py-2">{ss && <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${ss.farve}`}>{ss.tekst}</span>}</td>
                    <td className="px-2 py-2">{f.kanal ? KANAL[f.kanal] : <span className="font-medium text-red-700">Ingen kanal</span>}</td>
                    <td className={`whitespace-nowrap px-2 py-2 text-right tabular-nums ${Number(f.beloeb) < 0 ? "text-red-600" : ""}`}>{beloeb(f.beloeb)} kr.</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {svar.fakturaer.length === 0 && <p className="p-6 text-center text-sm text-slate-500">Ingen fakturaer her.</p>}
          {svar.afkortet && <p className="p-3 text-center text-xs text-slate-500">Viser de nyeste 500 – brug søgning eller filter.</p>}
        </div>
      )}
    </div>
  );
}
