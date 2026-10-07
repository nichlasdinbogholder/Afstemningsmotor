"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Tal } from "@/components/Maerker";
import { hent, type KundeRaekke } from "@/lib/api";
import { tidspunkt } from "@/lib/format";

export default function Kundeoversigt() {
  const [kunder, setKunder] = useState<KundeRaekke[] | null>(null);
  const [fejl, setFejl] = useState<string | null>(null);
  const [soeg, setSoeg] = useState("");
  const [kunMedFund, setKunMedFund] = useState(false);

  useEffect(() => {
    hent<KundeRaekke[]>("/api/kunder").then(setKunder).catch((e) => setFejl(String(e.message ?? e)));
  }, []);

  const viste = useMemo(() => {
    const s = soeg.trim().toLowerCase();
    return (kunder ?? [])
      .filter((k) => !s || k.navn.toLowerCase().includes(s) || k.kundenummer.toLowerCase().includes(s) || (k.cvr ?? "").includes(s))
      .filter((k) => !kunMedFund || k.aabne_fund.high + k.aabne_fund.medium + k.aabne_fund.low > 0)
      .sort((a, b) => b.aabne_fund.high - a.aabne_fund.high || b.aabne_fund.medium - a.aabne_fund.medium || a.navn.localeCompare(b.navn, "da", { numeric: true }));
  }, [kunder, soeg, kunMedFund]);

  if (fejl) return <p className="text-red-700">{fejl}</p>;
  if (!kunder) return <p className="text-slate-500">Henter kunder …</p>;

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Kunder</h1>
          <p className="text-sm text-slate-500">{viste.length} af {kunder.length} aktive kunder · flest alvorlige fund øverst</p>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={kunMedFund} onChange={(e) => setKunMedFund(e.target.checked)} />
            Kun kunder med åbne fund
          </label>
          <input
            value={soeg}
            onChange={(e) => setSoeg(e.target.value)}
            placeholder="Søg navn, kundenr. eller CVR"
            className="w-72 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Kunde</th>
              <th className="px-4 py-2">Kundenr.</th>
              <th className="px-2 py-2 text-center">Høj</th>
              <th className="px-2 py-2 text-center">Mellem</th>
              <th className="px-2 py-2 text-center">Lav</th>
              <th className="px-4 py-2">Sidst opdateret</th>
            </tr>
          </thead>
          <tbody>
            {viste.map((k) => (
              <tr key={k.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link href={`/kunde/?id=${k.id}`} className="font-medium text-slate-900 hover:underline">{k.navn}</Link>
                </td>
                <td className="px-4 py-2 text-slate-600">{k.kundenummer}</td>
                <td className="px-2 py-2 text-center"><Tal n={k.aabne_fund.high} farve="bg-red-100 text-red-800" /></td>
                <td className="px-2 py-2 text-center"><Tal n={k.aabne_fund.medium} farve="bg-amber-100 text-amber-800" /></td>
                <td className="px-2 py-2 text-center"><Tal n={k.aabne_fund.low} farve="bg-slate-100 text-slate-700" /></td>
                <td className="px-4 py-2">
                  {k.natkoersel ? (
                    <span className={k.natkoersel.status === "ok" ? "text-slate-600" : "font-medium text-red-700"}>
                      {k.natkoersel.status === "ok" ? "✓" : "⚠ fejl"} {tidspunkt(k.natkoersel.tidspunkt)}
                    </span>
                  ) : (
                    <span className="text-slate-400">aldrig</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {viste.length === 0 && <p className="p-6 text-center text-sm text-slate-500">Ingen kunder passer til søgningen.</p>}
      </div>
    </>
  );
}
