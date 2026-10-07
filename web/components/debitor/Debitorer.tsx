"use client";

import { useCallback, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";
import { beloeb, KANAL } from "@/lib/format";

type Debitor = { id: number; nummer: string; navn: string; cvr: string | null; email: string | null; ean: string | null;
                 erhverv: boolean; kanal: string | null; blokeret: boolean; note: string | null; aabent: string; aabne_fakturaer: number };

export default function Debitorer({ kundeId }: { kundeId: number }) {
  const [liste, setListe] = useState<Debitor[] | null>(null);
  const [soeg, setSoeg] = useState("");
  const [valgt, setValgt] = useState<Debitor | null>(null);
  const genindlaes = useCallback((q = "") => {
    hent<Debitor[]>(`/api/opkraevning/${kundeId}/debitorer${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setListe);
  }, [kundeId]);
  useEffect(() => genindlaes(), [genindlaes]);

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1">
        <form onSubmit={(e) => { e.preventDefault(); genindlaes(soeg); }} className="mb-3 flex">
          <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg navn, nr., e-mail eller CVR"
                 className="w-80 rounded-l-md border border-slate-300 bg-white px-3 py-1.5 text-sm" />
          <button className="rounded-r-md bg-slate-900 px-3 text-sm text-white">Søg</button>
        </form>
        {!liste ? <p className="text-slate-500">Henter debitorer …</p> : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
                <tr><th className="px-3 py-2">Nr.</th><th className="px-3 py-2">Navn</th><th className="px-3 py-2">E-mail</th>
                  <th className="px-3 py-2">CVR/EAN</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Kanal</th>
                  <th className="px-3 py-2 text-right">Åbent</th><th className="px-3 py-2" /></tr>
              </thead>
              <tbody>
                {liste.map((d) => (
                  <tr key={d.id} onClick={() => setValgt(d)} className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${valgt?.id === d.id ? "bg-slate-100" : ""}`}>
                    <td className="px-3 py-1.5">{d.nummer}</td><td className="px-3 py-1.5">{d.navn}</td>
                    <td className="px-3 py-1.5">{d.email ?? <span className="text-red-700">mangler</span>}</td>
                    <td className="px-3 py-1.5 text-slate-600">{[d.cvr, d.ean].filter(Boolean).join(" / ")}</td>
                    <td className="px-3 py-1.5">{d.erhverv ? "Erhverv" : "Privat"}</td>
                    <td className="px-3 py-1.5">{d.kanal ? KANAL[d.kanal] : d.ean ? "EAN" : d.email ? "E-mail" : <span className="text-red-700">Ingen</span>}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{Number(d.aabent) ? `${beloeb(d.aabent)} kr.` : ""}</td>
                    <td className="px-3 py-1.5">{d.blokeret && <span className="rounded bg-red-100 px-1.5 text-xs font-semibold text-red-800">Blokeret</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {liste.length === 0 && <p className="p-6 text-center text-sm text-slate-500">Ingen debitorer.</p>}
          </div>
        )}
      </div>
      {valgt && <DebitorPanel key={valgt.id} d={valgt} luk={() => setValgt(null)} gemt={(ny) => {
        setValgt(ny); setListe((l) => l?.map((x) => (x.id === ny.id ? { ...x, ...ny } : x)) ?? null);
      }} />}
    </div>
  );
}

function DebitorPanel({ d, luk, gemt }: { d: Debitor; luk: () => void; gemt: (d: Debitor) => void }) {
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
