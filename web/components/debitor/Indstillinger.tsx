"use client";

import { useEffect, useState } from "react";
import { hent } from "@/lib/api";
import { beloeb, dato, RYKKERTILSTAND } from "@/lib/format";

type Data = Record<string, string | number | boolean | number[] | null>;

export default function Indstillinger({ kundeId }: { kundeId: number }) {
  const [d, setD] = useState<Data | null>(null);
  useEffect(() => { hent<Data>(`/api/opkraevning/${kundeId}/indstillinger`).then(setD); }, [kundeId]);
  if (!d) return <p className="text-slate-500">Henter …</p>;
  const raekker: [string, React.ReactNode][] = [
    ["Rykkere", RYKKERTILSTAND[String(d.rykkere)] ?? String(d.rykkere)],
    ["Minimum for rykker", `${beloeb(d.minimum as string)} kr.`],
    ["Første rykker", `${d.foerste_rykker_efter_dage} dage efter forfald`],
    ["Dage mellem rykkere", `${d.dage_mellem_rykkere} (loven: mindst 10)`],
    ["Fordeling af indbetalinger", d.fordeling === "costs_first" ? "Gebyrer og renter først" : "Hovedstol først"],
    ["Erhvervsgrupper i e-conomic", (d.erhvervsgrupper as number[]).join(", ") || "ingen – alle behandles som private"],
    ["FI-kreditornummer", d.fi_kreditornummer ?? "mangler"],
    ["Konto for rykkergebyrer / renter", `${d.gebyrkonto ?? "mangler"} / ${d.rentekonto ?? "mangler"}`],
    ["Gebyraftale underskrevet", d.gebyraftale ? dato(String(d.gebyraftale)) : "nej – der kan ikke faktureres"],
    ["Inkassomandat", d.inkassomandat ? dato(String(d.inkassomandat)) : "nej – ingen inkassosager"],
    ["Automatisk inkasso", d.automatisk_inkasso ? "ja" : "nej (godkendes af en medarbejder)"],
    ["Minimum for inkasso", `${beloeb(d.inkasso_minimum as string)} kr.`],
  ];
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      <dl className="grid grid-cols-[16rem_1fr] gap-y-2 text-sm">
        {raekker.map(([k, v]) => <div key={k} className="contents"><dt className="text-slate-500">{k}</dt><dd>{v}</dd></div>)}
      </dl>
      <p className="mt-4 text-xs text-slate-500">Indstillingerne kan endnu kun ændres af en administrator på serveren. Lovens grænser (gebyr højst 100 kr., højst 3 rykkere, mindst 10 dage) kan aldrig ændres.</p>
    </div>
  );
}
