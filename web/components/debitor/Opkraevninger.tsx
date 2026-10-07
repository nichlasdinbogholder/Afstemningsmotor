"use client";

import { useEffect, useState } from "react";
import FakturaPanel from "@/components/debitor/FakturaPanel";
import { hent } from "@/lib/api";
import { beloeb, BETALINGSSTATUS, dato, KANAL } from "@/lib/format";

type Faktura = {
  id: number; debitornummer: string; debitor: string; fakturanummer: string; oprettet: string; forfald: string;
  beloeb: string; restbeloeb: string; betalingsstatus: string; kanal: string | null; sendstatus: string | null; rykkere: number;
};
type Svar = { antal: Record<string, number>; fakturaer: Faktura[]; afkortet: boolean };

const FILTRE: { id: string; tekst: string; advarsel?: boolean }[] = [
  { id: "alt", tekst: "Alt" }, { id: "forfaldet", tekst: "Forfaldet" }, { id: "ikke_betalt", tekst: "Ikke betalt" },
  { id: "delvist_betalt", tekst: "Delvist betalt" }, { id: "med_rykker", tekst: "Med rykker" },
  { id: "betalt", tekst: "Betalt" }, { id: "kreditnota", tekst: "Kreditnota" },
  { id: "ingen_kanal", tekst: "Ingen kanal", advarsel: true },
];

export default function Opkraevninger({ kundeId }: { kundeId: number }) {
  const [filter, setFilter] = useState("alt");
  const [soeg, setSoeg] = useState("");
  const [aktivSoeg, setAktivSoeg] = useState("");
  const [svar, setSvar] = useState<Svar | null>(null);
  const [valgt, setValgt] = useState<number | null>(null);

  useEffect(() => {
    const p = new URLSearchParams({ filter, ...(aktivSoeg ? { q: aktivSoeg } : {}) });
    hent<Svar>(`/api/opkraevning/${kundeId}/fakturaer?${p}`).then(setSvar);
  }, [kundeId, filter, aktivSoeg]);

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1">
        <form onSubmit={(e) => { e.preventDefault(); setAktivSoeg(soeg); }} className="mb-3 flex">
          <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg fakturanr., debitor eller debitornr."
                 className="w-80 rounded-l-md border border-slate-300 bg-white px-3 py-1.5 text-sm" />
          <button className="rounded-r-md bg-slate-900 px-3 text-sm text-white">Søg</button>
        </form>
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
              <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
                <tr>
                  <th className="px-3 py-2" /><th className="px-3 py-2">Debitornr.</th><th className="px-3 py-2">Debitor</th>
                  <th className="px-3 py-2">Fakturanr.</th><th className="px-3 py-2">Oprettet</th><th className="px-3 py-2">Betalingsdag</th>
                  <th className="px-3 py-2">Betalingsstatus</th><th className="px-3 py-2 text-center">Rykkere</th>
                  <th className="px-3 py-2">Kanal</th><th className="px-3 py-2 text-right">Fakturabeløb</th>
                </tr>
              </thead>
              <tbody>
                {svar.fakturaer.map((f) => {
                  const s = BETALINGSSTATUS[f.betalingsstatus] ?? { tekst: f.betalingsstatus, farve: "bg-slate-200" };
                  return (
                    <tr key={f.id} onClick={() => setValgt(f.id)}
                        className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${valgt === f.id ? "bg-slate-100" : ""}`}>
                      <td className="px-3 py-2"><span className="rounded bg-sky-800 px-1.5 py-0.5 text-xs font-semibold text-white">+71</span></td>
                      <td className="px-3 py-2">{f.debitornummer}</td>
                      <td className="px-3 py-2">{f.debitor}</td>
                      <td className="px-3 py-2">{f.fakturanummer}</td>
                      <td className="px-3 py-2">{dato(f.oprettet)}</td>
                      <td className="px-3 py-2">{dato(f.forfald)}</td>
                      <td className="px-3 py-2"><span className={`inline-block w-28 rounded px-2 py-0.5 text-center text-xs font-semibold ${s.farve}`}>{s.tekst}</span></td>
                      <td className="px-3 py-2 text-center">{f.rykkere || ""}</td>
                      <td className="px-3 py-2">{f.kanal ? KANAL[f.kanal] : <span className="font-medium text-red-700">Ingen kanal</span>}</td>
                      <td className={`px-3 py-2 text-right tabular-nums ${Number(f.beloeb) < 0 ? "text-red-600" : ""}`}>{beloeb(f.beloeb)} kr.</td>
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
      {valgt !== null && <FakturaPanel key={valgt} fakturaId={valgt} luk={() => setValgt(null)} />}
    </div>
  );
}
