"use client";

import { useCallback, useEffect, useState } from "react";
import { hent, send } from "@/lib/api";
import { beloeb, dato, SPAERRE, tidspunkt } from "@/lib/format";

type Rykker = { id: number; fakturanummer: string; debitor: string; debitornummer: string; nr: number; status: string; dato: string;
                sendt: string | null; restbeloeb: string; gebyr: string; rente: string; kompensation: string };
type Data = { i_koe: Rykker[]; sendt: Rykker[]; spaerrer: { fakturanummer: string; debitor: string; aarsag: string; tidspunkt: string;
              detaljer: Record<string, unknown> | null }[] };

export default function Rykkere({ kundeId }: { kundeId: number }) {
  const [data, setData] = useState<Data | null>(null);
  const [fejl, setFejl] = useState<string | null>(null);
  const genindlaes = useCallback(() => { hent<Data>(`/api/opkraevning/${kundeId}/rykkere`).then(setData); }, [kundeId]);
  useEffect(genindlaes, [genindlaes]);

  async function fjern(r: Rykker) {
    const note = window.prompt(`Fjern ${r.nr === 0 ? "den venlige påmindelse" : `rykker nr. ${r.nr}`} på faktura ${r.fakturanummer} til ${r.debitor}?\nSkriv hvorfor (fx "kunden ringede, betaler fredag"):`);
    if (note === null) return;
    try { await send(`/api/opkraevning/rykker/${r.id}/fjern`, { note }); setFejl(null); genindlaes(); }
    catch (e) { setFejl(e instanceof Error ? e.message : "Fejl"); }
  }

  if (!data) return <p className="text-slate-500">Henter rykkere …</p>;
  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-1 font-semibold">I kø <span className="font-normal text-slate-500">– sendes næste bankdag kl. 9, hvis ingen spærre er opstået</span></h2>
        {fejl && <p className="mb-2 text-sm text-red-700">{fejl}</p>}
        <Tabel raekker={data.i_koe} tom="Ingen rykkere i kø." handling={(r) => (
          <button onClick={() => fjern(r)} className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-red-50 hover:text-red-800">Fjern</button>
        )} />
      </section>
      <section>
        <h2 className="mb-1 font-semibold">Sendt</h2>
        <Tabel raekker={data.sendt} tom="Ingen rykkere sendt endnu." />
      </section>
      <section>
        <h2 className="mb-1 font-semibold">Ikke sendt – og hvorfor</h2>
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
              <tr><th className="px-3 py-2">Tidspunkt</th><th className="px-3 py-2">Faktura</th><th className="px-3 py-2">Debitor</th><th className="px-3 py-2">Årsag</th></tr>
            </thead>
            <tbody>
              {data.spaerrer.map((s, i) => (
                <tr key={i} className="border-b border-slate-100 last:border-0">
                  <td className="px-3 py-1.5 text-slate-500">{tidspunkt(s.tidspunkt)}</td>
                  <td className="px-3 py-1.5">{s.fakturanummer}</td><td className="px-3 py-1.5">{s.debitor}</td>
                  <td className="px-3 py-1.5">{SPAERRE[s.aarsag] ?? s.aarsag}{s.aarsag === "fjernet_af_medarbejder" && s.detaljer && ` – ${String(s.detaljer.note ?? "")} (${String(s.detaljer.af ?? "")})`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.spaerrer.length === 0 && <p className="p-4 text-center text-sm text-slate-500">Ingen spærrer registreret.</p>}
        </div>
      </section>
    </div>
  );
}

function Tabel({ raekker, tom, handling }: { raekker: Rykker[]; tom: string; handling?: (r: Rykker) => React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 text-left text-xs font-semibold text-slate-600">
          <tr><th className="px-3 py-2">Faktura</th><th className="px-3 py-2">Debitor</th><th className="px-3 py-2 text-center">Nr.</th>
            <th className="px-3 py-2">{handling ? "Sendes" : "Sendt"}</th><th className="px-3 py-2 text-right">Restbeløb</th>
            <th className="px-3 py-2 text-right">Gebyr</th><th className="px-3 py-2 text-right">Rente</th><th className="px-3 py-2 text-right">Komp.</th>
            {handling && <th />}</tr>
        </thead>
        <tbody>
          {raekker.map((r) => (
            <tr key={r.id} className="border-b border-slate-100 last:border-0">
              <td className="px-3 py-1.5">{r.fakturanummer}</td><td className="px-3 py-1.5">{r.debitor} <span className="text-slate-400">({r.debitornummer})</span></td>
              <td className="px-3 py-1.5 text-center">{r.nr === 0 ? <span title="Venlig påmindelse – uden gebyr" className="rounded bg-sky-100 px-1.5 text-xs font-semibold text-sky-800">Påmindelse</span> : r.nr}</td>
              <td className="px-3 py-1.5">{r.sendt ? tidspunkt(r.sendt) : dato(r.dato)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{beloeb(r.restbeloeb)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{beloeb(r.gebyr)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{beloeb(r.rente)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{Number(r.kompensation) > 0 ? beloeb(r.kompensation) : ""}</td>
              {handling && <td className="px-3 py-1.5 text-right">{handling(r)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {raekker.length === 0 && <p className="p-4 text-center text-sm text-slate-500">{tom}</p>}
    </div>
  );
}
