"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Debitorer from "@/components/debitor/Debitorer";
import Indstillinger from "@/components/debitor/Indstillinger";
import Opkraevninger from "@/components/debitor/Opkraevninger";
import Rykkere from "@/components/debitor/Rykkere";
import { hent } from "@/lib/api";
import { RYKKERTILSTAND } from "@/lib/format";

const FANER = [
  { id: "opkraevninger", tekst: "Opkrævninger", ikon: "▤" },
  { id: "rykkere", tekst: "Rykkere", ikon: "⏱" },
  { id: "debitorer", tekst: "Debitorer", ikon: "👥" },
  { id: "afbetaling", tekst: "Afbetaling", ikon: "↘" },
  { id: "indstillinger", tekst: "Indstillinger", ikon: "⚙" },
] as const;
type Fane = (typeof FANER)[number]["id"];

export default function Side() {
  return <Suspense fallback={<p className="text-slate-500">Henter …</p>}><Kundeomraade /></Suspense>;
}

function Kundeomraade() {
  const params = useSearchParams();
  const router = useRouter();
  const id = Number(params.get("id"));
  const fane = (params.get("fane") ?? "opkraevninger") as Fane;
  const [kunde, setKunde] = useState<{ navn: string; kundenummer: string; status: string } | null>(null);
  const [tilstand, setTilstand] = useState<string>("");

  useEffect(() => {
    hent<{ navn: string; kundenummer: string; status: string }>(`/api/kunder/${id}`).then(setKunde);
    hent<{ rykkere: string }>(`/api/opkraevning/${id}/indstillinger`).then((d) => setTilstand(d.rykkere));
  }, [id]);
  const vaelg = (f: Fane) => router.replace(`/debitorstyring/kunde/?id=${id}&fane=${f}`);

  if (!id) return <p className="text-red-700">Ingen kunde valgt.</p>;
  return (
    <div className="flex gap-6">
      <aside className="w-52 shrink-0">
        <Link href="/debitorstyring/" className="text-sm text-slate-500 hover:text-slate-900">← Alle kunder</Link>
        <div className="mt-3 mb-4">
          <p className="font-semibold leading-tight">{kunde?.navn ?? "…"}</p>
          <p className="text-xs text-slate-500">Kundenr. {kunde?.kundenummer} · rykkere: {RYKKERTILSTAND[tilstand] ?? tilstand}</p>
        </div>
        <nav className="flex flex-col gap-1">
          {FANER.map((f) => (
            <button key={f.id} onClick={() => vaelg(f.id)}
                    className={`flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm ${fane === f.id ? "bg-white font-semibold text-slate-900 shadow-sm ring-1 ring-slate-200" : "text-slate-600 hover:bg-white"}`}>
              <span className="w-5 text-center">{f.ikon}</span>{f.tekst}
            </button>
          ))}
        </nav>
      </aside>
      <section className="min-w-0 flex-1">
        {fane === "opkraevninger" && <Opkraevninger kundeId={id} />}
        {fane === "rykkere" && <Rykkere kundeId={id} />}
        {fane === "debitorer" && <Debitorer kundeId={id} />}
        {fane === "afbetaling" && (
          <p className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
            Afbetalingsordninger kommer i trin 5: en medarbejder opretter en ordning på en faktura, systemet danner raterne og stopper rykkerne.
          </p>
        )}
        {fane === "indstillinger" && <Indstillinger kundeId={id} />}
      </section>
    </div>
  );
}
