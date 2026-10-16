"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Afbetaling from "@/components/debitor/Afbetaling";
import FakturaSide from "@/components/debitor/FakturaSide";
import Indstillinger from "@/components/debitor/Indstillinger";
import Kunder from "@/components/debitor/Kunder";
import Opkraevninger from "@/components/debitor/Opkraevninger";
import Rykkere from "@/components/debitor/Rykkere";
import { hent } from "@/lib/api";
import { RYKKERTILSTAND } from "@/lib/format";

// Samme rækkefølge som i FarPay. "Kunder" er her KUNDENS kunder (debitorerne).
const FANER = [
  { id: "kunder", tekst: "Kunder", ikon: "👥" },
  { id: "opkraevninger", tekst: "Opkrævninger", ikon: "▤" },
  { id: "rykkere", tekst: "Rykkere", ikon: "⏱" },
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
  const raa = params.get("fane") ?? "opkraevninger";
  const fane = (raa === "debitorer" ? "kunder" : raa) as Fane;
  const faktura = params.get("faktura");
  const soegning = params.get("q") ?? "";
  const filter = params.get("filter") ?? "alt";
  const liste = (f: string, q: string) => `${f !== "alt" ? `&filter=${f}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  const [kunde, setKunde] = useState<{ navn: string; kundenummer: string; status: string } | null>(null);
  const [tilstand, setTilstand] = useState<{ rykkere: string; svar_email: string | null } | null>(null);

  useEffect(() => {
    hent<{ navn: string; kundenummer: string; status: string }>(`/api/kunder/${id}`).then(setKunde);
    hent<{ rykkere: string; svar_email: string | null }>(`/api/opkraevning/${id}/indstillinger`).then(setTilstand);
  }, [id]);
  const adresse = (f: Fane, ekstra = "") => `/debitorstyring/kunde/?id=${id}&fane=${f}${ekstra}`;
  const vaelg = (f: Fane) => router.push(adresse(f));

  if (!id) return <p className="text-red-700">Ingen kunde valgt.</p>;
  const mangler = tilstand && !tilstand.svar_email ? 1 : 0;
  return (
    <div className="flex gap-6">
      <aside className="w-52 shrink-0">
        <Link href="/debitorstyring/" className="text-sm text-slate-500 hover:text-slate-900">← Alle kunder</Link>
        <div className="mt-3 mb-4">
          <p className="font-semibold leading-tight">{kunde?.navn ?? "…"}</p>
          <p className="text-xs text-slate-500">Kundenr. {kunde?.kundenummer} · rykkere: {tilstand ? RYKKERTILSTAND[tilstand.rykkere] ?? tilstand.rykkere : ""}</p>
        </div>
        <nav className="flex flex-col gap-1">
          {FANER.map((f) => (
            <button key={f.id} onClick={() => vaelg(f.id)}
                    className={`flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm ${fane === f.id ? "bg-white font-semibold text-slate-900 shadow-sm ring-1 ring-slate-200" : "text-slate-600 hover:bg-white"}`}>
              <span className="w-5 text-center">{f.ikon}</span>{f.tekst}
              {f.id === "indstillinger" && mangler > 0 && (
                <span title="Svaradressen mangler" className="ml-auto rounded-full bg-red-600 px-2 text-xs font-semibold text-white">{mangler}</span>
              )}
            </button>
          ))}
        </nav>
      </aside>
      <section className="min-w-0 flex-1">
        {fane === "opkraevninger" && (faktura
          ? <FakturaSide key={faktura} fakturaId={Number(faktura)} tilbage={() => router.push(adresse("opkraevninger", liste(filter, soegning)))} />
          : <Opkraevninger key={`${soegning}|${filter}`} kundeId={id} soegning={soegning} filterStart={filter}
                           vaelg={(f, fl, q) => router.push(adresse("opkraevninger", `${liste(fl, q)}&faktura=${f}`))} />)}
        {fane === "rykkere" && <Rykkere kundeId={id} />}
        {fane === "kunder" && <Kunder kundeId={id} visFakturaer={(nr) => router.push(adresse("opkraevninger", `&q=${encodeURIComponent(nr)}`))} />}
        {fane === "afbetaling" && <Afbetaling kundeId={id} />}
        {fane === "indstillinger" && <Indstillinger kundeId={id} aendret={setTilstand} />}
      </section>
    </div>
  );
}
