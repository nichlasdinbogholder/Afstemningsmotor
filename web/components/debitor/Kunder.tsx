"use client";

import { useCallback, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";
import { beloeb, KANAL } from "@/lib/format";

type Debitor = { id: number; nummer: string; navn: string; cvr: string | null; email: string | null; ean: string | null;
                 adresse: string | null; postnr: string | null; by: string | null; erhverv: boolean; kanal: string | null;
                 kanal_nu: string | null; blokeret: boolean; note: string | null; aabent: string; aabne_fakturaer: number };

type Svar = { antal: Record<string, number>; debitorer: Debitor[]; afkortet: boolean };

const FILTRE: { id: string; tekst: string; farve: string }[] = [
  { id: "med_aabne", tekst: "Med åbne fakturaer", farve: "bg-sky-700" },
  { id: "ingen_kanal", tekst: "Ingen kanal", farve: "bg-red-600" },
  { id: "blokeret", tekst: "Blokeret", farve: "bg-red-600" },
  { id: "erhverv", tekst: "Erhverv", farve: "bg-slate-500" },
  { id: "alt", tekst: "Alt", farve: "" },
];

export default function Kunder({ kundeId, visFakturaer }: { kundeId: number; visFakturaer: (kundenr: string) => void }) {
  const [svar, setSvar] = useState<Svar | null>(null);
  const [filter, setFilter] = useState("alt");
  const [soeg, setSoeg] = useState("");
  const [aktivSoeg, setAktivSoeg] = useState("");
  const [valgt, setValgt] = useState<Debitor | null>(null);
  const params = new URLSearchParams({ filter, ...(aktivSoeg ? { q: aktivSoeg } : {}) });
  const genindlaes = useCallback(() => {
    const p = new URLSearchParams({ filter, ...(aktivSoeg ? { q: aktivSoeg } : {}) });
    hent<Svar>(`/api/opkraevning/${kundeId}/debitorer?${p}`).then(setSvar);
  }, [kundeId, filter, aktivSoeg]);
  useEffect(() => genindlaes(), [genindlaes]);

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <form onSubmit={(e) => { e.preventDefault(); setAktivSoeg(soeg); }} className="flex">
              <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg navn, nr., adresse, e-mail eller CVR"
                     className="w-72 rounded-l-md border border-slate-300 bg-white px-3 py-1.5 text-sm" />
              <button className="rounded-r-md bg-sky-700 px-3 text-sm text-white hover:bg-sky-800">Søg</button>
            </form>
            {FILTRE.map((f) => {
              const n = svar?.antal[f.id] ?? 0;
              return (
                <button key={f.id} onClick={() => setFilter(f.id)}
                        className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ${filter === f.id ? "bg-slate-200 font-medium text-slate-900" : "text-sky-700 hover:bg-slate-100"}`}>
                  {f.farve && <span className={`rounded-full px-2 text-xs font-semibold text-white ${n > 0 ? f.farve : "bg-slate-300"}`}>{n}</span>}
                  {f.tekst}{!f.farve && <span className="text-xs text-slate-400">{n}</span>}
                </button>
              );
            })}
          </div>
          <a href={`/api/opkraevning/${kundeId}/eksport/debitorer.csv?${params}`}
             title="Henter de viste kunder som en fil til Excel. Eksporten logges."
             className="rounded-md border border-slate-300 bg-white px-4 py-1.5 text-sm hover:bg-slate-50">Excel-eksport</a>
        </div>
        {!svar ? <p className="text-slate-500">Henter kunder …</p> : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
                <tr><th className="px-3 py-2" /><th className="px-3 py-2">Kundenr.</th><th className="px-3 py-2">Kunde</th>
                  <th className="px-3 py-2">Adresse</th><th className="px-3 py-2">Postnr. og by</th><th className="px-3 py-2">Kanal</th>
                  <th className="px-3 py-2 text-right">Åbent</th><th className="px-3 py-2" /></tr>
              </thead>
              <tbody>
                {svar.debitorer.map((d) => (
                  <tr key={d.id} onClick={() => setValgt(d)} className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${valgt?.id === d.id ? "bg-slate-100" : ""}`}>
                    <td className="px-3 py-2"><span className="rounded bg-sky-800 px-1.5 py-0.5 text-xs font-semibold text-white">+71</span></td>
                    <td className="px-3 py-2">{d.nummer}</td>
                    <td className="px-3 py-2">{d.navn}{d.erhverv && <span className="ml-2 text-xs text-slate-400">erhverv</span>}</td>
                    <td className="px-3 py-2">{d.adresse}</td>
                    <td className="px-3 py-2">{[d.postnr, d.by].filter(Boolean).join(" ")}</td>
                    <td className="px-3 py-2">{d.kanal_nu ? KANAL[d.kanal_nu] : <span className="font-medium text-red-700">Ingen kanal</span>}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{Number(d.aabent) ? `${beloeb(d.aabent)} kr.` : ""}</td>
                    <td className="px-3 py-2">{d.blokeret && <span className="rounded bg-red-100 px-1.5 text-xs font-semibold text-red-800">Blokeret</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {svar.debitorer.length === 0 && <p className="p-6 text-center text-sm text-slate-500">Ingen kunder her.</p>}
            {svar.afkortet && <p className="p-3 text-center text-xs text-slate-500">Viser 500 – brug søgning eller filter.</p>}
          </div>
        )}
      </div>
      {valgt && <DebitorPanel key={valgt.id} d={valgt} luk={() => setValgt(null)} visFakturaer={() => visFakturaer(valgt.nummer)}
        gemt={(ny) => { setValgt(ny); genindlaes(); }} />}
    </div>
  );
}

function DebitorPanel({ d, luk, gemt, visFakturaer }: { d: Debitor; luk: () => void; gemt: (d: Debitor) => void; visFakturaer: () => void }) {
  const [blokeret, setBlokeret] = useState(d.blokeret);
  const [kanal, setKanal] = useState(d.kanal ?? "");
  const [note, setNote] = useState(d.note ?? "");
  const [besked, setBesked] = useState<string | null>(null);

  async function gem() {
    try {
      const ny = await send<Debitor>(`/api/opkraevning/debitor/${d.id}`, { blokeret, kanal: kanal || null, note });
      gemt({ ...d, ...ny }); setBesked("Gemt");
    } catch (e) { setBesked(e instanceof Error ? e.message : "Fejl"); }
  }

  return (
    <aside className="sticky top-4 h-fit w-80 shrink-0 rounded-lg border border-slate-200 bg-white p-5 text-sm">
      <div className="mb-3 flex justify-between"><h2 className="font-semibold">{d.navn}</h2>
        <button onClick={luk} className="text-slate-400 hover:text-slate-900" aria-label="Luk">✕</button></div>
      <p className="text-slate-600">Kundenr. {d.nummer} · {d.erhverv ? "erhverv" : "privat"}{d.cvr && ` · CVR ${d.cvr}`}</p>
      <p className="text-slate-600">{d.email ?? <span className="text-red-700">ingen e-mail</span>}{d.ean && ` · EAN ${d.ean}`}</p>
      <p className="mb-2 text-slate-600">{d.aabne_fakturaer} åbne fakturaer{Number(d.aabent) > 0 && ` · ${beloeb(d.aabent)} kr.`}</p>
      <button onClick={visFakturaer} className="mb-4 text-sky-700 hover:underline">Se kundens fakturaer →</button>
      <p className="mb-4 text-xs text-slate-500">Stamdata (navn, e-mail, adresse) hentes fra kundens e-conomic og rettes dér.</p>
      <label className="mb-3 flex items-center gap-2">
        <input type="checkbox" checked={blokeret} onChange={(e) => setBlokeret(e.target.checked)} /> Blokér for rykkere
      </label>
      <label className="mb-3 block">Kanal
        <select value={kanal} onChange={(e) => setKanal(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Automatisk (EAN, ellers e-mail)</option>
          {Object.entries(KANAL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label className="mb-3 block">Note
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5" />
      </label>
      <button onClick={gem} className="rounded-md bg-slate-900 px-4 py-1.5 text-white hover:bg-slate-700">Gem</button>
      {besked && <span className="ml-3 text-slate-600">{besked}</span>}
    </aside>
  );
}
