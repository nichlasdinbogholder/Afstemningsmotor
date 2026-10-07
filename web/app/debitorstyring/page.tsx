"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { hent } from "@/lib/api";
import { beloeb, RYKKERTILSTAND } from "@/lib/format";

type Kunde = { id: number; navn: string; kundenummer: string; rykkere: string; aabne: number; forfaldne: number; forfaldent_beloeb: string };

export default function Debitorstyring() {
  const [kunder, setKunder] = useState<Kunde[] | null>(null);
  const [soeg, setSoeg] = useState("");
  const [alle, setAlle] = useState(false);

  useEffect(() => { hent<Kunde[]>("/api/opkraevning/kunder").then(setKunder); }, []);
  const viste = useMemo(() => (kunder ?? [])
    .filter((k) => alle || k.rykkere !== "off")
    .filter((k) => !soeg || k.navn.toLowerCase().includes(soeg.toLowerCase()) || k.kundenummer.includes(soeg)),
  [kunder, soeg, alle]);

  if (!kunder) return <p className="text-slate-500">Henter kunder …</p>;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Debitorstyring</h1>
          <p className="text-sm text-slate-500">Vælg en kunde for at se deres kunder, fakturaer og rykkere.</p>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={alle} onChange={(e) => setAlle(e.target.checked)} />
            Vis også kunder uden opkrævning
          </label>
          <input value={soeg} onChange={(e) => setSoeg(e.target.value)} placeholder="Søg kunde"
                 className="w-64 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm" />
        </div>
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Kunde</th><th className="px-4 py-2">Kundenr.</th><th className="px-4 py-2">Rykkere</th>
              <th className="px-4 py-2 text-right">Åbne fakturaer</th><th className="px-4 py-2 text-right">Forfaldne</th>
              <th className="px-4 py-2 text-right">Forfaldent beløb</th>
            </tr>
          </thead>
          <tbody>
            {viste.map((k) => (
              <tr key={k.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="px-4 py-2"><Link href={`/debitorstyring/kunde/?id=${k.id}`} className="font-medium hover:underline">{k.navn}</Link></td>
                <td className="px-4 py-2 text-slate-600">{k.kundenummer}</td>
                <td className="px-4 py-2 text-slate-600">{RYKKERTILSTAND[k.rykkere] ?? k.rykkere}</td>
                <td className="px-4 py-2 text-right">{k.aabne}</td>
                <td className={`px-4 py-2 text-right ${k.forfaldne ? "font-medium text-amber-700" : "text-slate-400"}`}>{k.forfaldne}</td>
                <td className="px-4 py-2 text-right tabular-nums">{beloeb(k.forfaldent_beloeb)} kr.</td>
              </tr>
            ))}
          </tbody>
        </table>
        {viste.length === 0 && (
          <p className="p-6 text-center text-sm text-slate-500">
            Ingen kunder har opkrævning slået til endnu. Sæt kundens rykkere til &quot;Prøvekørsel&quot; for at hente deres fakturaer.
          </p>
        )}
      </div>
    </>
  );
}
